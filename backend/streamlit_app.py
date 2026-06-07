import streamlit as st
import requests

st.set_page_config(page_title="Clutch Agent Chat", page_icon="🤖")

st.title("🤖 Clutch Live Chat Simulation")
st.markdown("Enter your **Clutch API Key** to chat directly with your indexed documents using Moss.")

# Sidebar for configuration
with st.sidebar:
    st.header("Widget Configuration")
    company_key = st.text_input("Clutch API Key", type="password", help="Found on your Clutch dashboard")
    backend_url = st.text_input("Backend API URL", value="http://localhost:8000/api")
    
    st.markdown("---")
    st.caption("This app simulates what the JavaScript widget does on a customer's website.")

# Chat state initialization
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history on app rerun
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# React to user input
if prompt := st.chat_input("Ask a question about your products..."):
    if not company_key:
        st.error("Please enter your API key in the sidebar first.")
    else:
        # Display user message in chat message container
        st.chat_message("user").markdown(prompt)
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        # Display assistant response in chat message container
        with st.chat_message("assistant"):
            with st.spinner("Searching manuals via Moss..."):
                try:
                    res = requests.post(
                        f"{backend_url.rstrip('/')}/chat",
                        json={"company_key": company_key, "query": prompt},
                        timeout=30
                    )
                    
                    if res.status_code == 200:
                        data = res.json()
                        st.markdown(data["answer"])
                        
                        # Add sources if available
                        if data.get("sources"):
                            sources_text = "\n\n*Sources: " + ", ".join(data["sources"]) + "*"
                            st.markdown(sources_text)
                            st.session_state.messages.append(
                                {"role": "assistant", "content": data["answer"] + sources_text}
                            )
                        else:
                            st.session_state.messages.append(
                                {"role": "assistant", "content": data["answer"]}
                            )
                    else:
                        err_msg = f"API Error {res.status_code}: {res.text}"
                        st.error(err_msg)
                        st.session_state.messages.append({"role": "assistant", "content": err_msg})
                
                except Exception as e:
                    err_msg = f"Connection failed. Is the FastAPI backend running? Error: {e}"
                    st.error(err_msg)
                    st.session_state.messages.append({"role": "assistant", "content": err_msg})
