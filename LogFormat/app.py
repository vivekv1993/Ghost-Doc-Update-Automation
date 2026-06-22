"""
Log Format Agent Web Interface (Gradio)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This module serves as the interactive graphical user interface (GUI) for the 
Log Format automation graph. Built with Gradio, it surfaces the LangGraph agent's 
internal state to the web, allowing engineers to interact with human-in-the-loop 
checkpoints safely.

Key Capabilities:
    - **Dynamic Routing:** Automatically toggles UI panels based on the graph's 
      current suspension point (e.g., missing info vs. review stage).
    - **Live XML Preview:** Converts YAML modifications into a compiled XML 
      preview in real-time as the user types.
    - **Sandboxed Execution:** Maps web inputs to isolated, thread-safe workspaces 
      using Jira Ticket IDs.
"""
import gradio as gr
import os
from LogFormat.agent import BASE_DIR, app as langgraph_app, create_sandbox_config
from LogFormat.xmlGenerator import UniversalXMLGenerationEngine
import xml.etree.ElementTree as ET
from xml.dom import minidom
import yaml
import json

# --- HELPER FUNCTIONS ---

def get_ui_state_updates(config):
    """
    Evaluates the current LangGraph state and returns Gradio UI visibility toggles.
    
    This function acts as the UI router. It checks which node the graph is currently 
    paused at (e.g., 'ask_human' or 'review_yaml') and generates the appropriate 
    state updates to hide or show the corresponding web components.

    :param config: The LangGraph configuration dictionary containing the thread_id.
    :return: A tuple of ``gr.update()`` objects configuring the status text, 
             clarification column, missing alert, review column, and editors.
    """
    state = langgraph_app.get_state(config)
    if not state.next:
        return (
            gr.update(value="Status: 🏁 Workflow Finished"),
            gr.update(visible=False), gr.update(), 
            gr.update(visible=False), gr.update()
        )
    next_node = state.next[0]
    
    if next_node == "ask_human":
        # Extract the missing fields to show the user
        missing = state.values.get("missing_attributes", [])
        missing_text = "### ⚠️ Missing Information Detected\n" + "\n".join([f"* {m}" for m in missing])
        
        return (
            gr.update(value="Status: 🟡 Waiting for Missing Info"),
            gr.update(visible=True), gr.update(value=missing_text),  # Show Clarification UI
            gr.update(visible=False), gr.update(), gr.update()                    # Hide Review UI
        )

    elif next_node == "review_yaml":
        yaml_content = state.values.get("yaml_string", "")
        clean_xml = ""
        # DIAGNOSTIC CHECK: If the agent didn't populate the string, show a warning instead of a blank box
        if not yaml_content:
            yaml_content = "# ⚠️ WARNING: The agent reached the review stage, but 'yaml_string' is empty or None in the graph state.\n# Check your yaml_compiler_node to ensure it saves the output to state['yaml_string']."
            print(yaml_content)

        clean_xml = update_xml_preview(yaml_content)

        return (
            gr.update(value="Status: 🔵 Ready for Review"),
            gr.update(visible=False), gr.update(),                   # Hide Clarification UI
            gr.update(visible=True), gr.update(value=yaml_content), gr.update(value=clean_xml)   # Show Review UI
        )
        
    else:
        return (
            gr.update(value="Status: 🟢 Inactive / Finished"),
            gr.update(visible=False), gr.update(),
            gr.update(visible=False), gr.update()
        )

def update_xml_preview(yaml_str):
    """
    Dynamically generates a live XML preview from the user's YAML input.
    
    Triggered on every keystroke in the Gradio YAML editor. It parses the current 
    YAML string, merges it with the schema map, and utilizes the 
    ``UniversalXMLGenerationEngine`` to render a formatted XML string.

    :param yaml_str: The raw YAML string from the Gradio code editor.
    :return: A pretty-printed HTML/XML string for the preview window, or an error 
             message if the YAML is currently invalid.
    """
    if not yaml_str or yaml_str.strip() == "":
        return ""
    try:
        import yaml
        yaml_dict = yaml.safe_load(yaml_str)
        # Assuming schema_map is globally available or loaded from disk
        with open(os.path.join(BASE_DIR, "schema_map.json"), "r") as f:
            schema_map = json.load(f)
            
        engine = UniversalXMLGenerationEngine(yaml_dict, schema_map)
        xml_root = engine.generate_xml()
        
        # Pretty print
        raw_xml = ET.tostring(xml_root, encoding="utf-8")
        pretty = minidom.parseString(raw_xml).toprettyxml(indent="    ")
        return "\n".join([line for line in pretty.split("\n") if line.strip()])
    except Exception as e:
        return f"Update XML error : {e}"

# --- EVENT HANDLERS ---

def start_agent(ticket_id: str, description: str):
    """
    Initializes the agent workspace and kicks off the extraction graph.
    
    Acts as the primary entry point for the UI. It creates the isolated sandbox 
    for the given ticket, triggers the LangGraph execution, and yields intermediate 
    loading states to the UI before returning the final routed component visibility.

    :param ticket_id: The unique Jira ticket identifier.
    :param description: The raw text instructions provided by the engineer.
    :yield: Sequences of ``gr.update()`` tuples to drive the UI loading states.
    """
    if not ticket_id or not description:
        gr.Warning("Please provide both a Ticket ID and a Description.")
        # Return a safe default if empty
        yield (
            gr.update(value="Status: ⚪ Standby"),
            gr.update(visible=False), gr.update(),
            gr.update(visible=False), gr.update(),
            gr.update()
        )
        return
        
    config = create_sandbox_config(ticket_id.strip())
    
    # 1. INSTANT UI UPDATE: Tell the user we are working on it
    yield (
        gr.update(value="Status: ⏳ Analyzing ticket and compiling YAML... (This may take a few seconds)"),
        gr.update(visible=False), gr.update(), # Hide Clarification UI
        gr.update(visible=False), gr.update(), gr.update()  # Hide Review UI
    )
    
    # 2. HEAVY LIFTING: Run the LangGraph/LLM process
    langgraph_app.invoke({"user_input": description}, config)
    
    # 3. FINAL UI UPDATE: Show the actual result (Clarification or Review)
    yield get_ui_state_updates(config)

def submit_clarification(ticket_id: str, clarification_text: str):
    """
    Injects missing context into the graph state and resumes execution.
    
    Triggered when the graph pauses at the ``ask_human`` node. It appends the 
    user's clarification to the original prompt, updates the state memory, and 
    resumes the graph workflow.

    :param ticket_id: The unique Jira ticket identifier mapping to the sandbox.
    :param clarification_text: The additional context provided by the engineer.
    :yield: Sequences of ``gr.update()`` tuples, handling either a successful 
            transition to the review stage or capturing system crashes.
    """
    config = create_sandbox_config(ticket_id.strip())
    state = langgraph_app.get_state(config)
    
    # 1. IMMEDIATE UI UPDATE (5 outputs)
    yield (
        gr.update(value="Status: ⏳ Processing Clarification..."),
        gr.update(visible=False), gr.update(), # Hide Clarification UI
        gr.update(visible=False), gr.update(), gr.update()  # Hide Review UI
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
            gr.update(visible=False), gr.update(), gr.update()                     # Keep Review UI hidden,
        )

def deploy_yaml(ticket_id: str, edited_yaml: str):
    """
    Approves the staged YAML, resumes the graph, and triggers downstream deployment.
    
    Triggered from the review UI. It injects the final, human-edited YAML string 
    back into the graph state, resumes execution through the deployment node, and 
    streams the execution logs back to the Gradio web interface.

    :param ticket_id: The unique Jira ticket identifier.
    :param edited_yaml: The finalized YAML string approved by the user.
    :yield: Sequences of ``gr.update()`` tuples transitioning from loading states 
            to the final deployment success/error logs.
    """
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

with gr.Blocks() as ui:
    gr.Markdown("# Log Updater")
    status_indicator = gr.Markdown("Status: ⚪ Standby")
    
    # 1. THE PERSISTENT CONTROL BAR
    with gr.Row():
        ticket_input = gr.Textbox(label="Jira Ticket ID", placeholder="ENG-123", scale=1)
        with gr.Column(scale=3):
            # Swapped Textbox for Code component for better YAML editing and native Tab support
            desc_input = gr.Code(
                label="[Temp] Paste/Edit Jira Description Here", 
                language="yaml", 
                lines=10
            )
        fetch_btn = gr.Button("🔍 Fetch & Compile", variant="primary", scale=1)
        
    gr.Markdown("---")

    # 2. STATE A: CLARIFICATION NEEDED (Hidden by default)
    with gr.Column(visible=False) as clarification_col:
        missing_alert = gr.Markdown(value="### ⚠️ Missing Information")
        clarification_input = gr.Textbox(label="Provide the missing details below:")
        submit_clarification_btn = gr.Button("Submit Clarification")
        
    # 3. STATE B: REVIEW & DEPLOY (Hidden by default)
    with gr.Column(visible=False) as review_col:
        with gr.Row():
            yaml_editor = gr.Code(
                label="Staged Production YAML (Editable)", 
                language="yaml", 
                interactive=True,
                lines=15
            )
            xml_viewer = gr.Code(
                label="Live XML Preview",
                language="html",
                interactive=False,
                lines=15
            )
        deploy_btn = gr.Button("🚀 Approve & Deploy to Master", variant="stop")
        
    # Reading the templates
    with open(os.path.join(BASE_DIR, "userTemplates.txt"), "r") as f:
        template = f.read()

    
    with gr.Column(visible=True) as help_col:
        with gr.Accordion("📋 Developer Quick Templates (Expand to copy snippets)", open=False):
                gr.Markdown(
                    template
                )

    # 4. STATE C: SUCCESS LOGS (Hidden by default)
    with gr.Column(visible=False) as success_col:
        output_log = gr.Textbox(label="Deployment Output Log", interactive=False, lines=5)

    # --- WIRING THE BUTTONS TO THE EVENTS ---
    
    # Clicking "Fetch" triggers start_agent, and updates the visibility of our columns
    fetch_btn.click(
        fn=start_agent, 
        inputs=[ticket_input, desc_input], 
        outputs=[status_indicator, clarification_col, missing_alert, review_col, yaml_editor, xml_viewer]
    )
    
    # Clicking "Submit" triggers the clarification handoff
    submit_clarification_btn.click(
        fn=submit_clarification,
        inputs=[ticket_input, clarification_input],
        outputs=[status_indicator, clarification_col, missing_alert, review_col, yaml_editor, xml_viewer]
    )
    
    # Clicking "Deploy" commits the YAML and shows the final logs
    deploy_btn.click(
        fn=deploy_yaml,
        inputs=[ticket_input, yaml_editor],
        outputs=[status_indicator, review_col, success_col, output_log]
    )

    # Change in yaml will edit a trigger
    yaml_editor.change(
        fn=update_xml_preview,
        inputs=[yaml_editor],
        outputs=[xml_viewer]
    )


if __name__ == "__main__":
    ui.launch(server_port=7960)
