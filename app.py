# STREAMLIT UI FOR AI DATA ANALYST
import matplotlib.pyplot as plt
import streamlit as st
import pandas as pd
import re
import io

from pathlib import Path
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

# Create LLM
llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0.7, max_tokens=500)

def get_df_info(df):
  """Generate detailed description of the DataFrame for LLM"""
  info_parts = [
    f"Shape: {df.shape[0]} rows × {df.shape[1]} columns",
    f"Columns: {', '.join(df.columns.tolist())}",
    ""
  ]

  for col in df.columns:
    dtype = df[col].dtype
    non_null = df[col].notna().sum()
    unique = df[col].nunique()
    sample = df[col].dropna().head(3).tolist()

    # Detect data patterns
    hints = []
    sample_str = str(sample[0]) if sample else ""
    if "$" in sample_str:
      hints.append("CURRENCY — clean with str.replace and regex=False before numeric ops")
    if "-" in sample_str and len(sample_str) == 10:
      hints.append("DATE — convert with pd.to_datetime")

    hint_str = f" ⚠️ {'; '.join(hints)}" if hints else ""
    info_parts.append(
      f"- {col} ({dtype}): {non_null} non-null, {unique} unique, "
      f"sample: {sample}{hint_str}"
    )

  return "\n".join(info_parts)


def read_data_file(uploaded_file):
  """Read any supported file format into a DataFrame"""
  name = uploaded_file.name.lower()
  
  if name.endswith(".csv"):
    df = pd.read_csv(uploaded_file)
  
  elif name.endswith(".tsv"):
    df = pd.read_csv(uploaded_file, sep="\t")
  
  elif name.endswith(".xlsx"):
    df = pd.read_excel(uploaded_file, engine="openpyxl")
  
  elif name.endswith(".xls"):
    df = pd.read_excel(uploaded_file, engine="xlrd")
  
  else:
    st.error("Unsupported file format")
    return None
  
  # Clean column names
  df.columns = df.columns.str.replace('\n', ' ').str.strip()
  
  # Remove duplicate header rows hiding as data
  header_mask = pd.Series([True] * len(df))
  for col in df.columns:
      header_mask &= (df[col].astype(str).str.strip() != col.strip())
  df = df[header_mask].reset_index(drop=True)
  
  return df
  

def load_prompt():
  return Path("prompts/analyst.txt").read_text().strip()


def create_agent_prompt(df_info):
  system_prompt = load_prompt().replace("{df_info}", df_info)

  return ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{question}")
  ])


def execute_code(df, code):
  """Execute pandas code in a sandboxed environment"""
  
  # Limiting globals available to LLM's code
  allowed_globals = {
    "pd": pd,
    "plt": plt,
    "df": df.copy(),
  }
  
  # Capture print output
  output_capture = io.StringIO()
  
  try:
    # Block dangerous operations
    dangerous = ["import os", "import sys", "open(", "exec(","eval(", "__import__", "subprocess", "shutil","requests", "urllib"]
    for d in dangerous:
      if d in code:
        return {"error": f"Blocked: '{d}' is not allowed"}
    
    # Execute with restricted globals
    exec_globals = {"__builtins__": {
      "print": lambda *args: output_capture.write(" ".join(str(a) for a in args) + "\n"),
      "len": len,
      "range": range,
      "round": round,
      "sum": sum,
      "min": min,
      "max": max,
      "sorted": sorted,
      "str": str,
      "int": int,
      "float": float,
      "list": list,
      "dict": dict,
      "abs": abs,
      "enumerate": enumerate,
    }}
    exec_globals.update(allowed_globals)
    
    exec(code, exec_globals)
    
    # Extract chart path from code
    figure = None
    if plt.get_fignums():
      figure = plt.gcf() # get current figure
    
    # Get printed output
    printed = output_capture.getvalue().strip()
    print("Printed of LLM")
    print(printed)
    
    # Get result variable if it exists
    result_val = exec_globals.get("result", None)
    print("Result Val: ", result_val)
    result_df = None

    if isinstance(result_val, pd.DataFrame):
      result_df = result_val
      result_str = f"Table with {len(result_val)} rows"
    elif isinstance(result_val, pd.Series):
      result_df = result_val.to_frame()
      result_str = f"Table with {len(result_val)} rows"
    else:
      result_str = str(result_val) if result_val else printed or "Code executed"

    return {
      "result": result_str,
      "result_df": result_df,
      "figure": figure
    }
  except Exception as e:
    return {"error": f"Execution error: {str(e)}", "code": code}
  

def clean_code(code: str):
  """Remove markdowns or import statements"""
  code = code.strip()

  # Remove markdown
  if "```" in code:
    parts = code.split("```")
    for part in parts:
      part = part.strip()
      if part.startswith("python"):
          part = part[6:].strip()
      # Check if this part looks like actual code
      if part and any(keyword in part for keyword in ["df", "result", "plt", "print", "="]):
        code = part
        break

  # Filter out any import statements
  cleaned_code = [
    line for line in code.split("\n")
    if not line.strip().startswith("import")
    and not line.strip().startswith("from")
    and "plt.savefig" not in line
    and "plt.close" not in line
    and "plt.show" not in line
  ]

  return "\n".join(cleaned_code)


def validate_code_columns(df, code):
  """Check if code references columns that don't exist"""
  referenced = re.findall(r"df\[['\"](.+?)['\"]\]", code)
  missing = [col for col in referenced if col not in df.columns]
  
  if missing:
    available = ", ".join(df.columns.tolist())
    return {
      "result": f"Column(s) not found: {', '.join(missing)}. Available columns: {available}",
      "result_df": None,
      "figure": None
    }
  return None

def ask_agent(df, question, llm, max_retries=2):
  """Create langchain chain"""
  df_info = get_df_info(df)
  prompt = create_agent_prompt(df_info)
  chain = prompt | llm | StrOutputParser()

  last_error = None
  
  # Silently try twice before asking user to rephrase
  for attempt in range(max_retries):
    # On retry, include the error so LLM can fix its approach
    if last_error and attempt > 0:
      retry_question = (
        f"{question}\n\n"
        f"PREVIOUS ATTEMPT FAILED with error: {last_error}\n"
        f"Write simpler code to avoid this error."
      )
    else:
      retry_question = question

    raw_response = chain.invoke({"question": retry_question})
    code = clean_code(raw_response.strip())
    
    if not code.strip():
      continue
    
    # Detect plain text responses (LLM refused or explained)
    first_line = code.strip().split("\n")[0]
    looks_like_code = any([
      "=" in first_line,
      first_line.startswith(("df", "plt", "result", "print", "#", "for ", "if ")),
    ])
    
    if not looks_like_code:
      return "", {
        "result": code,
        "result_df": None,
        "figure": None
      }
    
    # Validate columns exist before execution
    column_error = validate_code_columns(df, code)
    if column_error:
      return code, column_error
    
    # Execute
    result = execute_code(df, code)
    print("Result", result)
    
    if "error" not in result:
      print("No Error in result!")
      return code, result
    
    # If execution failed, try once more with error details
    last_error = result["error"]
    
  # If All retries failed — give a helpful message
  return code if code else "", {
    "result": "I couldn't analyze that. Could you try asking in a different way?",
    "result_df": None,
    "figure": None
  }

st.set_page_config(page_title="AI Data Analyst", page_icon="📊")
st.title("AI Data Analyst")
    
# Set Data Frame
if "df" not in st.session_state:
  st.session_state.df = None

# File uploader
with st.sidebar:
  st.header("Upload Data")
  uploaded_file = st.file_uploader("Data File", type=["csv", "xlsx", "xls", "tsv"])
  st.divider()
  show_code = st.toggle("Show generated code", value=False)

# Write first five rows to UI
if uploaded_file:
  st.session_state.df = read_data_file(uploaded_file)

if st.session_state.df is not None:
  st.subheader("Data Preview")
  st.dataframe(st.session_state.df.head())
  st.caption(f"{st.session_state.df.shape[0]} rows * {st.session_state.df.shape[1]} columns")
else:
  st.info("Upload your file (CSV/TSV/XLSX/XLS) and ask questions related to it.")

# create messages
if "messages" not in st.session_state:
  st.session_state.messages = []

# Show previous messages
for msg in st.session_state.messages:
  with st.chat_message(msg["role"]):
    st.write(msg["content"])
    if msg.get("table") is not None:
      st.dataframe(msg["table"])
    if msg.get("figure"):
      st.pyplot(msg["figure"])

# Chat Window
if question:= st.chat_input("Ask anything about your data"):
  with st.chat_message("user"):
    st.write(question.strip())
  
  # Append to chat messages
  st.session_state.messages.append({"role": "user", "content": question.strip()})

  
  with st.chat_message("assistant"):
    try:
      with st.spinner("Analyzing your data..."):
        # Ask LLM
        code, result = ask_agent(st.session_state.df, question.strip(), llm)
      
      # Code written by Agent
      if show_code:
        st.code(code.strip(), language="python")

      # AI responses
      if result.get("result_df") is not None:
        st.dataframe(result["result_df"])
      else:
        st.write(result["result"].strip())

      # If returned chart present it
      if (figure := result.get("figure")) is not None:
        st.pyplot(figure)
        plt.close(figure)

      st.session_state.messages.append({
        "role": "assistant", 
        "content": result["result"].strip(),
        "table": result.get("result_df", ""),
        "figure": figure
      })
    except Exception as e:
        st.error(f"An error occured. Error: {e}")




