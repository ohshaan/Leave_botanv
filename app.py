import streamlit as st
import openai
import requests
import json
import logging
from datetime import datetime
from rapidfuzz import fuzz
import re

# -------- SET UP LOGGING --------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
logger.info("Starting Streamlit ERP Leave Application Chatbot...")

# -------- CONFIGURATION (Credentials Hard-Coded) --------
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"],
ERP_BEARER_TOKEN = ""
EMP_API_URL = "http://117.247.187.131:8085/api/EmployeeMasterApi/HrmGetEmployeeDetails/"
LEAVE_API_URL = "http://117.247.187.131:8085/api/LeaveApplicationApi"
FILL_LEAVE_TYPE_URL = "http://117.247.187.131:8085/api/LeaveApplicationApi/FillLeaveType"
HISTORY_API_URL = "http://117.247.187.131:8085/api/LeaveApplicationApi/HrmGetLeaveApplicationDetails"

# -------- LOAD HELP TEXT --------
@st.cache_data
def load_help_doc():
    try:
        with open("leave_help.txt", "r", encoding="utf-8") as f:
            text = f.read()
            logger.info("Help document loaded (chars: %d)", len(text))
            return text
    except FileNotFoundError:
        logger.error("leave_help.txt not found")
        return "Help document not found. Please add leave_help.txt."

help_doc = load_help_doc()

# -------- ERP API CALLS (all cached per emp) --------
@st.cache_data(ttl=300)
def get_employee_details_cached(emp_id):
    url = f"{EMP_API_URL}?strEmp_ID_N={emp_id}"
    headers = {
        "Authorization": f"Bearer {ERP_BEARER_TOKEN}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json"
    }
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return {"error": "No employee found with that ID."}
    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=300)
def get_leave_types_cached(emp_id):
    params = {"Emp_ID_N": emp_id, "Cgm_ID_N": 1, "{}": ""}
    headers = {"Authorization": f"Bearer {ERP_BEARER_TOKEN}", "Accept": "application/json"}
    try:
        resp = requests.get(FILL_LEAVE_TYPE_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return {"error": "Unexpected response format."}
    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=300)
def get_leave_applications_cached(emp_id):
    str_filter = f"A.Emp_ID_N={emp_id} AND A.Ela_Status_N NOT IN (0,6) ORDER BY Ela_RefferNo_V"
    headers = {
        "Authorization": f"Bearer {ERP_BEARER_TOKEN}",
        "Accept": "application/json"
    }
    params = {"StrFilter": str_filter}
    try:
        resp = requests.post(HISTORY_API_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return {"error": "Unexpected response format."}
    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=180)
def get_leave_summary_cached(emp_id, leave_type_id, from_date, to_date):
    def to_str_date(d):
        if isinstance(d, str):
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                return dt.strftime("%d-%b-%Y")
            except ValueError:
                return d
        return d
    from_str = to_str_date(from_date)
    to_str = to_str_date(to_date)
    strsql = f"{emp_id},{leave_type_id},'{from_str}','{to_str}',0,0,1,0"
    headers = {"Authorization": f"Bearer {ERP_BEARER_TOKEN}", "Accept": "application/json"}
    params = {"StrSql": strsql}
    try:
        resp = requests.post(LEAVE_API_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return {"error": "No leave summary found for given parameters."}
    except Exception as e:
        return {"error": str(e)}

# ===== Helper Functions for Leave History & Formatting =====
def get_leaves_by_year(leave_history, year=None):
    year = year or datetime.now().year
    results = []
    for lh in leave_history:
        from_d = lh.get("LeaveGrid_Ela_FromDate_D", "")
        if not from_d:
            continue
        try:
            y = datetime.strptime(from_d.split("T")[0], "%Y-%m-%d").year
            if y == year:
                results.append(lh)
        except Exception:
            continue
    return results

def get_leaves_by_month(leave_history, month=None, year=None):
    now = datetime.now()
    month = month or now.month
    year = year or now.year
    results = []
    for lh in leave_history:
        from_d = lh.get("LeaveGrid_Ela_FromDate_D", "")
        if not from_d:
            continue
        try:
            dt = datetime.strptime(from_d.split("T")[0], "%Y-%m-%d")
            if dt.year == year and dt.month == month:
                results.append(lh)
        except Exception:
            continue
    return results

def format_leave_list(leaves):
    if not leaves:
        return "No leave applications found for this period."
    lines = []
    for lh in leaves:
        ref = lh.get("LeaveGrid_Ela_RefferNo_V", "N/A")
        ltype = lh.get("LeaveGrid_Lvm_Description_V", "N/A")
        from_d = lh.get("LeaveGrid_Ela_FromDate_D", "").split("T")[0]
        to_d = lh.get("LeaveGrid_Ela_ToDate_D", "").split("T")[0]
        days = lh.get("LeaveGrid_Ela_Tot", 0)
        status = lh.get("LeaveGrid_Status", "N/A")
        lines.append(f"- Ref {ref}: {ltype}, {from_d} to {to_d} ({days} day(s)) — **{status}**")
    return "\n".join(lines)

def get_leave_by_ref(leave_history, ref_partial):
    for lh in leave_history:
        if ref_partial in str(lh.get("LeaveGrid_Ela_RefferNo_V", "")):
            return lh
    return None

def get_approved_leaves(leave_history, year=None):
    results = []
    for lh in leave_history:
        status = lh.get("LeaveGrid_Status", "").strip().lower()
        if status == "approved":
            from_d = lh.get("LeaveGrid_Ela_FromDate_D", "")
            if not year:
                results.append(lh)
            else:
                try:
                    y = datetime.strptime(from_d.split("T")[0], "%Y-%m-%d").year
                    if y == year:
                        results.append(lh)
                except Exception:
                    continue
    return results

# --- FUZZY MATCH UTILITY ---
def fuzzy_match(user_input, keywords, threshold=85):
    for kw in keywords:
        if fuzz.partial_ratio(user_input, kw) >= threshold:
            return True
    return False

# -------- OPENAI SETUP --------
openai.api_key = OPENAI_API_KEY
client = openai.OpenAI(api_key=OPENAI_API_KEY)

functions = [
    {
        "type": "function",
        "function": {
            "name": "get_employee_details",
            "description": "Fetch ERP employee details using employee ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "emp_id": {"type": "string", "description": "Employee ID"}
                },
                "required": ["emp_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_leave_types",
            "description": "Fetch all leave types available for an employee",
            "parameters": {
                "type": "object",
                "properties": {
                    "emp_id": {"type": "string", "description": "Employee ID"}
                },
                "required": ["emp_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_leave_applications",
            "description": "Fetch all leave applications for a given employee (excluding status 0 and 6)",
            "parameters": {
                "type": "object",
                "properties": {
                    "emp_id": {"type": "string", "description": "Employee ID"}
                },
                "required": ["emp_id"]
            }
        }
    }
]

def handle_function_call(call):
    name = call.name
    args = call.arguments if not isinstance(call.arguments, str) else json.loads(call.arguments)
    if name == "get_employee_details":
        return get_employee_details_cached(args.get("emp_id", ""))
    if name == "get_leave_types":
        return get_leave_types_cached(args.get("emp_id", ""))
    if name == "get_leave_applications":
        return get_leave_applications_cached(args.get("emp_id", ""))
    return {"error": "Unknown function."}

# ======== STREAMLIT UI & MAIN LOGIC ========
st.title("ERP Leave Application Chatbot")

raw_emp = st.query_params.get("emp_id", None)
if isinstance(raw_emp, list):
    emp_id_param = raw_emp[0]
else:
    emp_id_param = raw_emp

last_seen = st.session_state.get("last_emp")
if emp_id_param is not None:
    if last_seen is None:
        st.session_state["last_emp"] = emp_id_param
    elif emp_id_param != last_seen:
        for k in list(st.session_state.keys()):
            if k != "last_emp":
                del st.session_state[k]
        st.session_state["last_emp"] = emp_id_param
        st.experimental_rerun()

emp_id = st.session_state.get("last_emp")

if emp_id and "session_loaded" not in st.session_state:
    profile_data = get_employee_details_cached(emp_id)
    st.session_state["employee_profile"] = profile_data

    leave_types_data = get_leave_types_cached(emp_id)
    st.session_state["leave_types"] = leave_types_data if isinstance(leave_types_data, list) else []

    leave_history_data = get_leave_applications_cached(emp_id)
    st.session_state["leave_history"] = leave_history_data if isinstance(leave_history_data, list) else []

    today_str = datetime.now().strftime("%Y-%m-%d")
    summaries = {}
    for lt in st.session_state["leave_types"]:
        lpd_id = lt.get("Lpd_ID_N")
        if lpd_id is None:
            continue
        summary = get_leave_summary_cached(emp_id, str(lpd_id), today_str, today_str)
        summaries[lpd_id] = summary
    st.session_state["leave_summaries"] = summaries

    st.session_state["session_loaded"] = True
    logger.info("Cached profile, leave types, leave history, and leave_summaries for Emp_ID=%s", emp_id)

profile = st.session_state.get("employee_profile", {})
leave_types = st.session_state.get("leave_types", [])
leave_history = st.session_state.get("leave_history", [])
leave_summaries = st.session_state.get("leave_summaries", {})

# --- GREETING LOGIC ---
if "greeted" not in st.session_state:
    full_name = profile.get("Emp_EFullName_V", "").strip() if isinstance(profile, dict) else ""
    greeting_name = full_name if full_name else "there"
    greeting = f"Hello, {greeting_name}! How can I assist you today?"
    st.chat_message("assistant").markdown(greeting)
    st.session_state["greeted"] = True

if "messages" not in st.session_state:
    sys_prompt = (
        "You are an HR assistant. The user can ask about leave, policy, attachments, or any employee profile details "
        "(like job post, shift, company, reporting manager, RP expiry date, nationality, pay type, designation, etc.). "
        "You have access to this employee's full profile, available leave types, leave summaries, and the help document. "
        "Use only these data fields when answering questions. "
        "If a field is not available, reply 'Not available'. "
        "If the question is about procedure, use the help document below.\n\n"
        "HELP DOCUMENT:\n"
        f"{help_doc}\n\n"
        "EMPLOYEE PROFILE:\n"
        f"{json.dumps(st.session_state.get('employee_profile', {}), indent=2)}\n\n"
        "LEAVE TYPES:\n"
        f"{json.dumps(st.session_state.get('leave_types', []), indent=2)}\n\n"
        "LEAVE SUMMARIES:\n"
        f"{json.dumps(st.session_state.get('leave_summaries', {}), indent=2)}"
    )
    st.session_state["messages"] = [{"role": "system", "content": sys_prompt}]

for past in st.session_state["messages"][1:]:
    with st.chat_message(past["role"]):
        st.markdown(past.get("content", ""))

user_input = st.chat_input("Ask anything about leave, your profile, or manager…")
if not user_input:
    st.stop()

logger.info("User: %s", user_input)
st.session_state["messages"].append({"role": "user", "content": user_input})
with st.chat_message("user"):
    st.markdown(user_input)

lower = user_input.strip().lower()


year = datetime.now().year

# ================== MAIN INTENT RESOLUTION (ORDERED!) ==================
# Enhanced handler for procedural leave application queries
apply_procedure_re = re.search(r"how (do i|can i|to) apply for (.+?) leave", lower)
general_apply_procedure_re = re.search(r"how (do i|can i|to) apply for leave", lower)
procedure_keywords = [
    "procedure to apply leave",
    "how to apply leave",
    "leave application procedure",
    "apply for leave process"
]

if apply_procedure_re:
    leave_type_query = apply_procedure_re.group(2).strip()
    
    user_msg = (
        f"Please explain the procedure for applying for {leave_type_query} leave, "
        "based strictly on the provided help document."
    )
    
    special_system_prompt = (
        "You are an HR assistant. "
        "Answer strictly based on the following HELP DOCUMENT about leave application procedures. "
        "Do not mention employee data, leave history, or balances. "
        "If the answer is not in the document, say 'Information not available in the help document.'\n\n"
        f"HELP DOCUMENT:\n{help_doc}\n"
    )
    
    messages = [
        {"role": "system", "content": special_system_prompt},
        {"role": "user", "content": user_msg}
    ]
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages
    )
    
    assistant_text = response.choices[0].message.content or "Sorry, I could not find that information."
    
    st.session_state["messages"].append({"role": "user", "content": user_msg})
    st.session_state["messages"].append({"role": "assistant", "content": assistant_text})
    
    with st.chat_message("assistant"):
        st.markdown(assistant_text)
    st.stop()

elif general_apply_procedure_re or any(kw in lower for kw in procedure_keywords):
    user_msg = (
        "Please explain the general procedure for applying leave, "
        "based strictly on the provided help document."
    )
    
    special_system_prompt = (
        "You are an HR assistant. "
        "Answer strictly based on the following HELP DOCUMENT about leave application procedures. "
        "Do not mention employee data, leave history, or balances. "
        "If the answer is not in the document, say 'Information not available in the help document.'\n\n"
        f"HELP DOCUMENT:\n{help_doc}\n"
    )
    
    messages = [
        {"role": "system", "content": special_system_prompt},
        {"role": "user", "content": user_msg}
    ]
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages
    )
    
    assistant_text = response.choices[0].message.content or "Sorry, I could not find that information."
    
    st.session_state["messages"].append({"role": "user", "content": user_msg})
    st.session_state["messages"].append({"role": "assistant", "content": assistant_text})
    
    with st.chat_message("assistant"):
        st.markdown(assistant_text)
    st.stop()

# --- 1. Explicit leave application block ---
apply_re = re.search(
    r"\b(?:can\s+i\s+)?apply\s+for\s+(\d+)\s*(?:day|days)?\s*([a-zA-Z ]+?)\s*leave\b",
    lower
)
if apply_re:
    num_days = int(apply_re.group(1))
    leave_type_raw = apply_re.group(2).strip().upper()
    matched = None
    for lt in leave_types:
        desc = lt.get("Lvm_Description_V", "").strip().upper()
        if leave_type_raw in desc or desc in leave_type_raw:
            matched = lt
            break

    if not matched:
        reply = f"Could not find a leave type matching '{leave_type_raw.title()}'."
        st.session_state["pending_leave_application"] = None
    else:
        lpd_id = matched.get("Lpd_ID_N")
        summary = leave_summaries.get(lpd_id, {})
        try:
            balance = float(summary.get("Balance", 0))
        except (ValueError, TypeError):
            balance = 0
        desc = matched.get("Lvm_Description_V", "").title()
        if num_days <= balance:
            reply = f"Yes, you can apply for {num_days} days of {desc}."
            st.session_state["pending_leave_application"] = None
        else:
            reply = (
                f"No, you only have {balance} days available for {desc}. "
                f"You cannot apply for {num_days} days."
            )
            st.session_state["pending_leave_application"] = {
                "num_days": num_days,
                "leave_type": leave_type_raw
            }
    with st.chat_message("assistant"):
        st.markdown(reply)

    st.session_state["pending_leave_application"] = None
    st.session_state["last_draft_leave"] = None
    st.stop()


# --- 2. User clarification - e.g. "for casual leave" ---
for_type_re = re.search(r"for\s+([a-zA-Z ]+?)\s*leave\b", lower)
if for_type_re and st.session_state.get("pending_leave_application"):
    prev = st.session_state["pending_leave_application"]
    num_days = prev.get("num_days", None)
    leave_type_raw = for_type_re.group(1).strip().upper()
    matched = None
    for lt in leave_types:
        desc = lt.get("Lvm_Description_V", "").strip().upper()
        if leave_type_raw in desc or desc in leave_type_raw:
            matched = lt
            break
    if matched and num_days is not None:
        lpd_id = matched.get("Lpd_ID_N")
        summary = leave_summaries.get(lpd_id, {})
        try:
            balance = float(summary.get("Balance", 0))
        except (ValueError, TypeError):
            balance = 0
        desc = matched.get("Lvm_Description_V", "").title()
        if num_days <= balance:
            reply = f"Yes, you can apply for {num_days} days of {desc}."
        else:
            reply = (
                f"No, you only have {balance} days available for {desc}. "
                f"You cannot apply for {num_days} days."
            )
        st.session_state["pending_leave_application"] = None
    else:
        reply = f"Could not find a leave type matching '{leave_type_raw.title()}'."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- 3. Apply for X day leave (ambiguous) ---
short_apply_re = re.search(r"apply\s+for\s+(\d+)\s*(?:day|days)?\s*leave\b", lower)
if short_apply_re and not re.search(r"[a-zA-Z]+", short_apply_re.group(0)[short_apply_re.end(1):]):
    num_days = int(short_apply_re.group(1))
    eligible_types = []
    for lt in leave_types:
        lpd_id = lt.get("Lpd_ID_N")
        desc = lt.get("Lvm_Description_V", "").title()
        summary = leave_summaries.get(lpd_id, {})
        try:
            balance = float(summary.get("Balance", 0))
        except (ValueError, TypeError):
            balance = 0
        if num_days <= balance:
            eligible_types.append(desc)
    if len(eligible_types) == 1:
        reply = f"Yes, you can apply for {num_days} days of {eligible_types[0]}."
        st.session_state["pending_leave_application"] = None
    elif len(eligible_types) > 1:
        reply = (
            f"You are eligible to apply for {num_days} days under the following leave types: "
            + ", ".join(eligible_types) + ".\nPlease specify which leave type you want."
        )
        st.session_state["pending_leave_application"] = {
            "num_days": num_days,
            "leave_type": None
        }
    else:
        reply = f"You do not have enough balance for any leave type for {num_days} days."
        st.session_state["pending_leave_application"] = None
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()
# --- Generic check for enough leave balance when no reference number is provided ---
if ("enough leave" in lower or "enough balance" in lower or "get approved" in lower 
    or "sufficient leave" in lower or "sufficient balance" in lower) and not re.search(r"(?:lp|ref)[^\d]*(\d{3,})", lower):
    
    leave = leave_history[-1] if leave_history else None
    if not leave:
        reply = "No leave applications found to check balance."
    else:
        leave_type = leave.get("LeaveGrid_Lvm_Description_V", "").strip()
        days_requested = 0
        try:
            days_requested = float(leave.get("LeaveGrid_Ela_Tot", 0))
        except (ValueError, TypeError):
            days_requested = 0
        
        matched_type = next((lt for lt in leave_types if leave_type.lower() in lt.get("Lvm_Description_V", "").lower()), None)
        leave_balance = 0
        if matched_type:
            lpd_id = matched_type.get("Lpd_ID_N")
            summary = leave_summaries.get(lpd_id, {})
            try:
                leave_balance = float(summary.get("Balance", 0))
            except (ValueError, TypeError):
                leave_balance = 0

        if leave_balance >= days_requested and days_requested > 0:
            reply = (
                f"Yes, you have enough balance to get approval for your latest leave application.\n\n"
                f"- Leave Type: {leave_type}\n"
                f"- Days Requested: {days_requested}\n"
                f"- Your Current Balance: {leave_balance}"
            )
        elif days_requested == 0:
            reply = "Your latest leave application does not specify any days requested."
        else:
            reply = (
                f"No, you do not have enough balance to get approval for your latest leave application.\n\n"
                f"- Leave Type: {leave_type}\n"
                f"- Days Requested: {days_requested}\n"
                f"- Your Current Balance: {leave_balance}"
            )

    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- 4. Reference check for application eligibility ---
ref_check = re.search(r"(?:lp|ref)[^\d]*(\d{3,})", lower)
if ref_check and ("enough leave" in lower or "enough balance" in lower or "get approved" in lower or "sufficient leave" in lower or "sufficient balance" in lower):
    ref_partial = ref_check.group(1)
    leave = get_leave_by_ref(leave_history, ref_partial)
    if not leave:
        reply = f"Could not find leave application with reference {ref_partial}."
    else:
        leave_type = leave.get("LeaveGrid_Lvm_Description_V", "").strip()
        days_requested = float(leave.get("LeaveGrid_Ela_Tot", 0))
        matched_type = next((lt for lt in leave_types if leave_type.lower() in lt.get("Lvm_Description_V", "").lower()), None)
        leave_balance = 0
        if matched_type:
            lpd_id = matched_type.get("Lpd_ID_N")
            summary = leave_summaries.get(lpd_id, {})
            try:
                leave_balance = float(summary.get("Balance", 0))
            except (ValueError, TypeError):
                leave_balance = 0
        if leave_balance >= days_requested:
            reply = (f"Yes, you have enough balance to get approval for this application.\n\n"
                     f"- Leave Type: {leave_type}\n"
                     f"- Days Requested: {days_requested}\n"
                     f"- Your Current Balance: {leave_balance}")
        else:
            reply = (f"No, you do not have enough balance to get approval for this application.\n\n"
                     f"- Leave Type: {leave_type}\n"
                     f"- Days Requested: {days_requested}\n"
                     f"- Your Current Balance: {leave_balance}")
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- 5. Draft letter/request approval blocks ---
if ("draft a letter" in lower or "requesting to approve" in lower):
    ref_match = re.search(r"(lp|ref)?\s*(\d{3,})", lower)
    if not ref_match:
        leave = leave_history[-1] if leave_history else None
    else:
        ref_partial = ref_match.group(2)
        leave = get_leave_by_ref(leave_history, ref_partial)
    manager_name = profile.get("Emp_EmployeeReportsDesc_V", "Not available")
    manager_email = profile.get("Emp_EmailID_V", "Not available")
    your_name = profile.get("Emp_EFullName_V", "Not available")
    your_position = profile.get("Dsm_Desc_V", "Not available")
    your_department = profile.get("Dpm_Desc_V", "Not available")
    company_name = profile.get("Cmp_Name_V", "Not available")
    if leave is None:
        reply = "Could not find the specified leave application."
    else:
        ltype = leave.get("LeaveGrid_Lvm_Description_V", "N/A")
        from_d = leave.get("LeaveGrid_Ela_FromDate_D", "").split("T")[0]
        to_d = leave.get("LeaveGrid_Ela_ToDate_D", "").split("T")[0]
        days = leave.get("LeaveGrid_Ela_Tot", 0)
        ref = leave.get("LeaveGrid_Ela_RefferNo_V", "N/A")
        today = datetime.now().strftime("%Y-%m-%d")
        reply = f"""
**To:** {manager_name} (<{manager_email}>)

Subject: Request for Approval of Leave Application (Ref: {ref})

Dear {manager_name},

I hope this message finds you well. I am writing to formally request your approval for my leave application referenced as {ref}.

**Details of Leave Application:**
- Leave Type: {ltype}
- Requested Dates: {from_d} to {to_d}
- Total Days: {days}

Due to [brief reason, e.g., health reasons], I was unable to attend work during the above period. I have ensured all necessary handover arrangements for my responsibilities.

I kindly request your approval of this leave request. Please let me know if you need any further information.

Thank you for your attention.

Sincerely,  
{your_name}  
{your_position}, {your_department}  
{company_name}  
Date: {today}
"""
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- 6. Specific type leave balance query block ---
for lt in leave_types:
    desc = lt.get("Lvm_Description_V", "").lower()
    short = desc.split()[0]
    # Try to match phrases like "how many sick leave left", "casual leave left", etc.
    if f"{short} leave" in lower and "left" in lower:
        balance = leave_summaries.get(lt.get("Lpd_ID_N"), {}).get("Balance", 0)
        reply = f"You have {balance} days of {desc.title()} remaining."
        st.session_state["messages"].append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)
        st.stop()

# ================== END MAIN INTENT RESOLUTION ==================
airticket_re = re.search(r"air ?ticket", lower)
if airticket_re:
    # Check if user specified leave type
    leave_type_mentioned = None
    for lt in leave_types:
        desc = lt.get("Lvm_Description_V", "").lower()
        if desc in lower:
            leave_type_mentioned = lt
            break

    if leave_type_mentioned:
        lpd_id = leave_type_mentioned.get("Lpd_ID_N")
        summary = leave_summaries.get(lpd_id, {})
        if summary.get("Airticket") == "1" or summary.get("Airticket") == 1:
            percent = summary.get("AirTicketPercent", "N/A")
            reply = (
                f"You are eligible for an air ticket for {leave_type_mentioned.get('Lvm_Description_V')} leave. "
                f"Air ticket reimbursement percent: {percent}%."
            )
        else:
            reply = f"You are not eligible for an air ticket for {leave_type_mentioned.get('Lvm_Description_V')} leave."
    else:
        # No specific leave type mentioned: summarize all eligible types
        eligible_types = []
        for lt in leave_types:
            lpd_id = lt.get("Lpd_ID_N")
            summary = leave_summaries.get(lpd_id, {})
            if summary.get("Airticket") == "1" or summary.get("Airticket") == 1:
                eligible_types.append(lt.get("Lvm_Description_V", "Unknown"))

        if eligible_types:
            reply = (
                "You are eligible for air tickets under the following leave types: "
                + ", ".join(eligible_types)
                + "."
            )
        else:
            reply = "You are not eligible for air tickets under any leave type according to your profile."

    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# ------------ FUZZY SHORTCUT BLOCKS ---------------

how_many_leaves_keywords = [
    "how many leaves did i apply",
    "leaves did i apply this year",
    "leaves did i take this year",
    "how many leaves have i taken this year",
    "number of leaves this year",
    "total leaves this year"
]
if fuzzy_match(lower, how_many_leaves_keywords):
    leaves = get_leaves_by_year(leave_history, year)
    reply = f"You have applied for {len(leaves)} leaves this year."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

leave_month_keywords = [
    "did i apply for any leaves this month",
    "leaves this month",
    "did i take leave this month",
    "leave applications this month",
    "leaves in current month"
]
if fuzzy_match(lower, leave_month_keywords):
    leaves = get_leaves_by_month(leave_history)
    reply = format_leave_list(leaves)
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()
who_approves_keywords = [
    "who can approve my leave",
    "who approves my leaves",
    "who is the leave approver",
    "who can approve my leaves",
    "who approves leave",
    "leave approval authority"
]
if fuzzy_match(lower, who_approves_keywords, threshold=80):
    manager_name = profile.get("Emp_EmployeeReportsDesc_V", None)
    if manager_name and manager_name.lower() not in ["not available", ""]:
        reply = f"Your leave requests can be approved by your reporting manager, {manager_name}."
    else:
        reply = "The reporting manager information is not available in your profile."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()


leave_keywords = [
    "all my leaves",
    "all my leaves i have applied for",
    "all leaves",
    "show me all my leave applications",
    "all my previous leave applications",
    "leave applications",
    "all leave applications",
    "what are those",
    "which are these leaves",
    "what leaves did i take this year",
    "list my leaves",
    "what were my leaves this year",
    "my leaves for this year",
    "leaves for this year",
    "leaves this year",
    "show my leaves this year",
    "which leaves did i take this year",
    "leaves applied this year",
    "my leaves taken this year",
    "which leaves have i taken this year"
]
if fuzzy_match(lower, leave_keywords, threshold=80):
    reply = format_leave_list(leave_history)
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

last_approved_leave_keywords = [
    "last approved leave",
    "most recent approved leave",
    "latest approved leave",
    "previous approved leave"
]
if fuzzy_match(lower, last_approved_leave_keywords):
    approved_leaves = get_approved_leaves(leave_history)
    if not approved_leaves:
        reply = "No approved leave found in your history."
    else:
        latest = max(
            approved_leaves,
            key=lambda x: x.get("LeaveGrid_Ela_FromDate_D", "")
        )
        from_d = latest.get("LeaveGrid_Ela_FromDate_D", "").split("T")[0]
        to_d = latest.get("LeaveGrid_Ela_ToDate_D", "").split("T")[0]
        days = latest.get("LeaveGrid_Ela_Tot", 0)
        ref = latest.get("LeaveGrid_Ela_RefferNo_V", "N/A")
        ltype = latest.get("LeaveGrid_Lvm_Description_V", "N/A")
        reply = (
            f"Your last approved leave was Ref {ref}: {ltype}, "
            f"from {from_d} to {to_d} ({days} day(s))."
        )
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()
# --- Check if user asks if they have a specific leave type (e.g. annual leave) ---
specific_leave_check = re.search(r"do i have (.+?) leave", lower)
if specific_leave_check:
    leave_type_query = specific_leave_check.group(1).strip().lower()
    matched_leave = None
    for lt in leave_types:
        desc = lt.get("Lvm_Description_V", "").lower()
        if leave_type_query in desc:
            matched_leave = lt
            break

    if matched_leave:
        lpd_id = matched_leave.get("Lpd_ID_N")
        summary = leave_summaries.get(lpd_id, {})
        balance = summary.get("Balance", 0)
        eligible = summary.get("Eligible", 0)
        reply = f"You have {balance} days balance for {matched_leave.get('Lvm_Description_V')}."
    else:
        reply = f"I could not find information about '{leave_type_query}' leave in your profile."

    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

last_leave_keywords = [
    "last leave",
    "most recent leave",
    "previous leave",
    "latest leave"
]
if fuzzy_match(lower, last_leave_keywords):
    if not leave_history:
        reply = "⚠️ Could not fetch your leave history."
    else:
        try:
            latest = max(leave_history, key=lambda x: x.get("LeaveGrid_Ela_FromDate_D", ""))
            from_d = latest.get("LeaveGrid_Ela_FromDate_D", "").split("T")[0]
            to_d = latest.get("LeaveGrid_Ela_ToDate_D", "").split("T")[0]
            days = latest.get("LeaveGrid_Ela_Tot", 0)
            status = latest.get("LeaveGrid_Status", "N/A")
            ref = latest.get("LeaveGrid_Ela_RefferNo_V", "N/A")
            ltype = latest.get("LeaveGrid_Lvm_Description_V", "N/A")
            reply = (
                f"Your last leave was Ref {ref}: {ltype}, "
                f"from {from_d} to {to_d} ({days} day(s)) — **{status}**"
            )
        except Exception:
            reply = "⚠️ Unable to determine your last leave."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

leave_balance_keywords = [
    "leave balance",
    "how many leaves left",
    "balance leaves",
    "my leave balance",
    "available leaves",
    "leaves remaining"
]
if fuzzy_match(lower, leave_balance_keywords):
    if not leave_types:
        reply = "⚠️ Could not fetch your leave types."
    else:
        lines = ["**Your current leave balances:**"]
        for lt in leave_types:
            lpd_id = lt.get("Lpd_ID_N")
            lt_desc = lt.get("Lvm_Description_V", "N/A")
            summary = leave_summaries.get(lpd_id, {})
            if isinstance(summary, dict) and "error" in summary:
                continue
            balance = summary.get("Balance", 0)
            eligible = summary.get("Eligible", 0)
            lines.append(f"- {lt_desc}: Balance **{balance}**, Eligible **{eligible}**")
        reply = "\n\n".join(lines)
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()
leave_policy_keywords = [
    "leave policy",
    "my leave policy",
    "what is my leave policy",
    "show my leave policy",
    "explain leave policy",
    "leave entitlements",
    "leave rules",
    "leave policy details",
    "policy for leaves",
    "leave policy information"
]
if fuzzy_match(lower, leave_policy_keywords, threshold=80):
    policy_name = profile.get("Lph_Desc_V") or profile.get("Emp_LeavePolicy_V") or "Not specified"
    leave_types_list = leave_types  # Should already be loaded per your code
    leave_summaries_list = []
    # If you have leave_summaries as a dict (as in your session), convert to list:
    if isinstance(leave_summaries, dict):
        leave_summaries_list = list(leave_summaries.values())
    elif isinstance(leave_summaries, list):
        leave_summaries_list = leave_summaries

    def format_leave_policy(policy_name, leave_types, leave_summaries):
        summaries_by_atm = {}
        for s in leave_summaries:
            if "Atm_TypeID_N" in s:
                summaries_by_atm[str(s.get("Atm_TypeID_N"))] = s
        lines = [f"**Your leave policy:** {policy_name}", "\n**Entitlements:**"]
        lines.append("| Leave Type | Eligible (days/year) | Attach Required | Paid/Unpaid | Air Ticket |")
        lines.append("|------------|----------------------|----------------|-------------|------------|")
        for lt in leave_types:
            desc = lt.get("Lvm_Description_V", "N/A")
            atm_id = str(lt.get("Atm_ID_N"))
            attach_required = "Yes" if str(lt.get("Lvm_AttachRequired_N", "0")) == "1" else "No"
            summary = summaries_by_atm.get(atm_id)
            eligible = summary.get("Eligible") if summary else None
            try:
                eligible = int(float(eligible))
            except Exception:
                pass
            eligible_str = f"{eligible}" if eligible else "—"
            paid = summary.get("Paid") if summary else None
            unpaid = summary.get("UnPaid") if summary else None
            if paid == "1":
                paid_str = "Paid"
            elif unpaid == "1":
                paid_str = "Unpaid"
            else:
                paid_str = "—"
            airticket = summary.get("Airticket") if summary else None
            airpercent = summary.get("AirTicketPercent") if summary else None
            if airticket == "1":
                air_str = f"Yes ({airpercent}%)" if airpercent else "Yes"
            else:
                air_str = "No"
            lines.append(f"| {desc} | {eligible_str} | {attach_required} | {paid_str} | {air_str} |")
        return "\n".join(lines)

    reply = format_leave_policy(policy_name, leave_types_list, leave_summaries_list)
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()


contact_manager_keywords = [
    "how can i contact my manager",
    "how can i contact him",
    "how do i reach my manager",
    "contact my reporting manager",
    "manager contact",
    "contact details for my manager"
]
if fuzzy_match(lower, contact_manager_keywords, threshold=80):
    manager_name = profile.get("Emp_EmployeeReportsDesc_V", "Not available")
    manager_email = profile.get("Emp_ManagerEmailID_V", None)
    manager_mobile = profile.get("Emp_ManagerMobileNo_V", None)
    if not manager_email:
        manager_email = profile.get("Emp_EmployeeReportsEmailID_V", None)
    if not manager_mobile:
        manager_mobile = profile.get("Emp_EmployeeReportsMobileNo_V", None)
    contact_lines = [f"Contact information for your reporting manager, {manager_name}:"]
    if manager_email:
        contact_lines.append(f"- Email: {manager_email}")
    if manager_mobile:
        contact_lines.append(f"- Mobile: {manager_mobile}")
    if not manager_email and not manager_mobile:
        contact_lines.append("No contact details available in your profile.")
    reply = "\n".join(contact_lines)
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- Job Post / Designation ---
job_post_keywords = [
    "job post", "job title", "designation", "what is my job post", "what is my designation", "position"
]
if any(kw in lower for kw in job_post_keywords):
    job_post = profile.get("Dsm_Desc_V") or profile.get("Emp_Designation_V") or "Not available"
    reply = f"Your job post is: {job_post}."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- Department ---
department_keywords = [
    "department", "which department", "my department", "where do i work", "which team", "department do i work"
]
if any(kw in lower for kw in department_keywords):
    department = profile.get("Dpm_Desc_V") or profile.get("Emp_Department_V") or "Not available"
    reply = f"You work in the {department} department."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- Reporting Manager ---
manager_keywords = [
    "manager", "reporting manager", "who is my manager", "who is my reporting manager", "supervisor"
]
if any(kw in lower for kw in manager_keywords):
    manager = profile.get("Emp_EmployeeReportsDesc_V") or profile.get("Emp_Manager_V") or "Not available"
    reply = f"Your reporting manager is: {manager}."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- Shift Policy ---
shift_keywords = [
    "shift policy", "my shift policy", "what is my shift", "shift", "work shift"
]
if any(kw in lower for kw in shift_keywords):
    shift = (
        profile.get("Emp_ShiftPolicy_V")
        or profile.get("Emp_Shift_V")
        or profile.get("Sfh_ShiftName_V")
        or profile.get("Sfh_ShiftCode_V")
        or "Not available"
    )
    reply = f"Your shift policy is: {shift}."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()

# --- Visa Type ---
visa_type_keywords = [
    "visa", "visa type", "what is my visa", "what is my visa type", "work visa", "residence permit", "rp type"
]

if any(kw in lower for kw in visa_type_keywords):
    visa_type = (
        profile.get("Emp_VisaType_V")
        or profile.get("EmpVisatype_Desc_V")
        or profile.get("Emp_VisaTypeID_N")
        or "Not available"
    )
    reply = f"Your visa type is: {visa_type}."
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()
# --- Cancel or Reschedule Leave Block ---
cancel_keywords = [
    "cancel my leave", "can i cancel my leave", "withdraw my leave", "can i withdraw my leave",
    "cancel approved leave", "withdraw approved leave", "can i cancel approved leave",
    "can i withdraw approved leave", "can i cancel my approved leave",
    "can i reschedule my leave", "reschedule my leave", "can i change my leave dates",
    "modify my leave", "change leave dates", "edit leave application"
]
if any(kw in lower for kw in cancel_keywords):
    # Check if the user refers to a specific leave (by ref) or just the latest approved
    ref_match = re.search(r"(lp|ref)[^\d]*(\d{3,})", lower)
    leave = None
    if ref_match:
        ref_partial = ref_match.group(2)
        leave = get_leave_by_ref(leave_history, ref_partial)
    else:
        # Default: latest approved leave
        approved_leaves = get_approved_leaves(leave_history)
        leave = max(approved_leaves, key=lambda x: x.get("LeaveGrid_Ela_FromDate_D", ""), default=None)
    
    if not leave:
        reply = "No approved leave application found to cancel or reschedule."
    else:
        editable = str(leave.get("Editable", "0"))
        ref = leave.get("LeaveGrid_Ela_RefferNo_V", "N/A")
        ltype = leave.get("LeaveGrid_Lvm_Description_V", "N/A")
        from_d = leave.get("LeaveGrid_Ela_FromDate_D", "").split("T")[0]
        to_d = leave.get("LeaveGrid_Ela_ToDate_D", "").split("T")[0]
        status = leave.get("LeaveGrid_Status", "N/A")
        if editable == "1" and status.lower() == "approved":
            reply = (
                f"Yes, you can cancel or reschedule your approved leave (Ref {ref}: {ltype}, {from_d} to {to_d}).\n"
                "Please use the ERP self-service portal to cancel or request a change for this leave application."
            )
        else:
            reason = []
            if editable != "1":
                reason.append("it is locked for editing")
            if status.lower() != "approved":
                reason.append(f"status is '{status}'")
            reply = (
                f"No, you cannot cancel or reschedule your leave (Ref {ref}: {ltype}, {from_d} to {to_d}) "
                f"because {' and '.join(reason)}. "
                "Contact HR if you believe this is incorrect."
            )
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
    st.stop()


# ----------- DEFAULT: ALWAYS FALL BACK TO LLM WITH ALL DATA -----------
response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=st.session_state["messages"],
    tools=functions,
    tool_choice="auto"
)
msg = response.choices[0].message

if getattr(msg, "function_call", None):
    logger.info("LLM requested function call: %s", msg.function_call.name)
    result = handle_function_call(msg.function_call)
    result_str = json.dumps(result)
    logger.info("Function '%s' returned: %s", msg.function_call.name, result_str)

    st.session_state["messages"].append({
        "role": "function",
        "name": msg.function_call.name,
        "content": result_str
    })

    followup = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=st.session_state["messages"] + [{
            "role": "function",
            "name": msg.function_call.name,
            "content": result_str
        }],
        tools=functions,
        tool_choice="auto"
    )
    assistant_text = followup.choices[0].message.content or ""
    logger.info("Final assistant response: %s", assistant_text)
    st.session_state["messages"].append({"role": "assistant", "content": assistant_text})
    with st.chat_message("assistant"):
        st.markdown(assistant_text)
else:
    assistant_text = msg.content or ""
    logger.info("Assistant response (no function call): %s", assistant_text)
    st.session_state["messages"].append({"role": "assistant", "content": assistant_text})
    with st.chat_message("assistant"):
        st.markdown(assistant_text)
