import gradio as gr
from agent import app as langgraph_app, create_sandbox_config

# --- HELPER FUNCTIONS ---

def get_ui_state_updates(config):
    """
    Checks the current state of the graph and returns the visibility toggles 
    for the Gradio UI components based on which brake is currently active.
    """
    state = langgraph_app.get_state(config)
    next_node = state.next[0] if state.next else None
    
    if next_node == "ask_human":
        # Extract the missing fields to show the user
        missing = state.values.get("missing_attributes", [])
        missing_text = "### ⚠️ Missing Information Detected\n" + "\n".join([f"* {m}" for m in missing])
        
        return (
            gr.update(value="Status: 🟡 Waiting for Missing Info"),
            gr.update(visible=True), gr.update(value=missing_text),  # Show Clarification UI
            gr.update(visible=False), gr.update()                    # Hide Review UI
        )
        
    elif next_node == "review_yaml":
        yaml_content = state.values.get("yaml_string", "")
        # DIAGNOSTIC CHECK: If the agent didn't populate the string, show a warning instead of a blank box
        if not yaml_content:
            yaml_content = "# ⚠️ WARNING: The agent reached the review stage, but 'yaml_string' is empty or None in the graph state.\n# Check your yaml_compiler_node to ensure it saves the output to state['yaml_string']."
            print(yaml_content)

        return (
            gr.update(value="Status: 🔵 Ready for Review"),
            gr.update(visible=False), gr.update(),                   # Hide Clarification UI
            gr.update(visible=True), gr.update(value=yaml_content)   # Show Review UI
        )
        
    else:
        return (
            gr.update(value="Status: 🟢 Inactive / Finished"),
            gr.update(visible=False), gr.update(),
            gr.update(visible=False), gr.update()
        )

# --- EVENT HANDLERS ---

def start_agent(ticket_id: str, description: str):
    if not ticket_id or not description:
        gr.Warning("Please provide both a Ticket ID and a Description.")
        # Return a safe default if empty
        yield (
            gr.update(value="Status: ⚪ Standby"),
            gr.update(visible=False), gr.update(),
            gr.update(visible=False), gr.update()
        )
        return
        
    config = create_sandbox_config(ticket_id.strip())
    
    # 1. INSTANT UI UPDATE: Tell the user we are working on it
    yield (
        gr.update(value="Status: ⏳ Analyzing ticket and compiling YAML... (This may take a few seconds)"),
        gr.update(visible=False), gr.update(), # Hide Clarification UI
        gr.update(visible=False), gr.update()  # Hide Review UI
    )
    
    # 2. HEAVY LIFTING: Run the LangGraph/LLM process
    langgraph_app.invoke({"user_input": description}, config)
    
    # 3. FINAL UI UPDATE: Show the actual result (Clarification or Review)
    yield get_ui_state_updates(config)

def submit_clarification(ticket_id: str, clarification_text: str):
    config = create_sandbox_config(ticket_id.strip())
    state = langgraph_app.get_state(config)
    
    # 1. IMMEDIATE UI UPDATE (5 outputs)
    yield (
        gr.update(value="Status: ⏳ Processing Clarification..."),
        gr.update(visible=False), gr.update(), # Hide Clarification UI
        gr.update(visible=False), gr.update()  # Hide Review UI
    )
    
    try:
        original_input = state.values.get("user_input", "")
        new_input = f"{original_input}\n[User Clarification]: {clarification_text}"
        
        langgraph_app.update_state(config, {"user_input": new_input}, as_node="ask_human")
        
        for _ in langgraph_app.stream(None, config):
            pass
            
        # 2. FINAL UI UPDATE (5 outputs)
        # If it succeeds (or asks for MORE info), this handles the UI routing perfectly
        yield get_ui_state_updates(config)
        
    except Exception as e:
        # 3. CRASH CAPTURE (5 outputs)
        yield (
            gr.update(value="Status: 🔴 System Error"),
            gr.update(visible=True),                                  # Bring back Clarification UI
            gr.update(value=f"### ⚠️ System Crash:\n```text\n{str(e)}\n```"), # Show the Python error
            gr.update(visible=False), gr.update()                     # Keep Review UI hidden
        )

def deploy_yaml(ticket_id: str, edited_yaml: str):
    config = create_sandbox_config(ticket_id.strip())
    
    # 1. IMMEDIATE UI UPDATE (4 outputs)
    yield (
        gr.update(value="Status: 🚀 Executing Deployment... Please wait."),
        gr.update(visible=False),  # Hide review_col
        gr.update(visible=False),  # Hide success_col
        gr.update(value="")        # Clear the output_log textbox
    )
    
    try:
        langgraph_app.update_state(config, {"yaml_string": edited_yaml}, as_node="review_yaml")
        
        logs = ""
        for event in langgraph_app.stream(None, config):
            for node_name, output in event.items():
                if isinstance(output, dict) and "deployment_status" in output:
                    logs += f"✅ {output['deployment_status']}\n"
        
        if not logs.strip():
            logs = "✅ Deployment executed (no logs returned)."

        # 2. FINAL UI UPDATE (4 outputs)
        yield (
            gr.update(value="Status: 🟢 Deployed Successfully"),
            gr.update(visible=False),  # Keep review_col hidden
            gr.update(visible=True),   # Show success_col
            gr.update(value=logs)      # Put the text inside output_log
        )
        
    except Exception as e:
        # 3. ERROR UPDATE (4 outputs)
        yield (
            gr.update(value="Status: 🔴 Deployment Failed"),
            gr.update(visible=True),   # Bring back review_col
            gr.update(visible=True),   # Show success_col to display the error
            gr.update(value=f"❌ Error:\n{str(e)}") # Put the error in output_log
        )
# --- UI LAYOUT DESIGN ---

with gr.Blocks(title="Akamai Config Gatekeeper", theme=gr.themes.Default(neutral_hue="slate")) as ui:
    gr.Markdown("# 🎛️ Akamai Logging Agent Control Plane")
    status_indicator = gr.Markdown("Status: ⚪ Standby")
    
    # 1. THE PERSISTENT CONTROL BAR
    with gr.Row():
        ticket_input = gr.Textbox(label="Jira Ticket ID", placeholder="ENG-123", scale=1)
        # TODO: Remove this description box later when MCP handles ingestion
        desc_input = gr.Textbox(label="[Temp] Paste Jira Description Here", lines=2, scale=3)
        fetch_btn = gr.Button("🔍 Fetch & Compile", variant="primary", scale=1)
        
    gr.Markdown("---")
    
    # 2. STATE A: CLARIFICATION NEEDED (Hidden by default)
    with gr.Column(visible=False) as clarification_col:
        missing_alert = gr.Markdown(value="### ⚠️ Missing Information")
        clarification_input = gr.Textbox(label="Provide the missing details below:")
        submit_clarification_btn = gr.Button("Submit Clarification")
        
    # 3. STATE B: REVIEW & DEPLOY (Hidden by default)
    with gr.Column(visible=False) as review_col:
        yaml_editor = gr.Code(
            label="Staged Production YAML (Editable)", 
            language="yaml", 
            interactive=True,
            lines=15
        )
        deploy_btn = gr.Button("🚀 Approve & Deploy to Master", variant="stop")
        
    # 4. STATE C: SUCCESS LOGS (Hidden by default)
    with gr.Column(visible=False) as success_col:
        output_log = gr.Textbox(label="Deployment Output Log", interactive=False, lines=5)

    # --- WIRING THE BUTTONS TO THE EVENTS ---
    
    # Clicking "Fetch" triggers start_agent, and updates the visibility of our columns
    fetch_btn.click(
        fn=start_agent, 
        inputs=[ticket_input, desc_input], 
        outputs=[status_indicator, clarification_col, missing_alert, review_col, yaml_editor]
    )
    
    # Clicking "Submit" triggers the clarification handoff
    submit_clarification_btn.click(
        fn=submit_clarification,
        inputs=[ticket_input, clarification_input],
        outputs=[status_indicator, clarification_col, missing_alert, review_col, yaml_editor]
    )
    
    # Clicking "Deploy" commits the YAML and shows the final logs
    deploy_btn.click(
        fn=deploy_yaml,
        inputs=[ticket_input, yaml_editor],
        outputs=[status_indicator, review_col, success_col, output_log]
    )

if __name__ == "__main__":
    ui.launch(server_port=7960)
