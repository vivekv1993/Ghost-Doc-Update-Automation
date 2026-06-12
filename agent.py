from typing import TypedDict, List, Optional, Literal
import yaml
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langchain_core.prompts import ChatPromptTemplate
from langchain.chat_models import init_chat_model
import os
import dotenv
from graph import CompilerPayload

dotenv.load_dotenv()

# GRAPH STATE MEMORY

class AgentState(TypedDict):
    user_input: str                           # Raw prompt from the engineer
    parsed_payload: Optional[CompilerPayload] # The structured Pydantic object from the LLM
    missing_attributes: List[str]             # Checklist of fields the user forgot
    yaml_string: Optional[str]                # Final compiled YAML (will be None for now)
    validation_error: Optional[str]           # Execution errors (will be None for now)


# GRAPH NODES

def unified_extractor_node(state: AgentState) -> dict:
    """
    Cognitive node: Extracts clean schema models from raw text inputs.
    """
    raw_prompt = state["user_input"]
    system_instruction = (
        "You are an expert compiler extraction assistant for an Akamai logging platform.\n"
        "Your task is to analyze the user's request and map it strictly into the schema.\n\n"
        "CRITICAL ROUTING RULES:\n"
        "1. If the user asks to add/append a changelog, set action_type to 'append_changelog' "
        "and ONLY fill the changelog_payload. You MUST leave logline_payload completely null.\n"
        "2. If the user asks to modify a log field, set action_type to 'update_logline' "
        "and ONLY fill the logline_payload. You MUST leave changelog_payload completely null.\n"
        "3. BE LAZY: Never hallucinate nested data. If a field, bitmask, or sub_field is not "
        "explicitly mentioned by the user, you MUST leave it null. Do not generate empty nested objects.\n"
        "4. NEVER hallucinate IDs. If a user mentions 'r77', the target log line ID is 'r'."
    )
  
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        ("human", "{input}")
    ])
    
    llm = init_chat_model(
        "openrouter/free",
        model_provider="openai",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.0
    )
    structured_llm = llm.with_structured_output(CompilerPayload)
    
    extractor_chain = prompt | structured_llm
    extracted_data: CompilerPayload = extractor_chain.invoke({"input": raw_prompt})
    
    # Quick check for missing metadata validation
    missing = []
    if extracted_data.action_type == "append_changelog":
        payload = extracted_data.changelog_payload
        if not payload or not payload.author or not payload.ghost_version:
            missing.append("changelog details (author/version/date)")
    elif extracted_data.action_type == "update_logline":
        # Assign it first, then check it!
        sorted_data = extracted_data.logline_payload
        if not sorted_data or not sorted_data.id:
            missing.append("log line target ID token (e.g., 'r', 'f')")

    return {
        "parsed_payload": extracted_data,
        "missing_attributes": missing
    }

def yaml_compiler_node(state: AgentState) -> dict:
    """
    Deterministic node: Converts the validated Pydantic model into a YAML string.
    """
    # model_dump(exclude_none=True) strips out all the nulls
    payload_dict = state["parsed_payload"].model_dump(exclude_none=True)
    
    # Dump to a clean YAML string
    yaml_str = yaml.dump(payload_dict, default_flow_style=False, sort_keys=False)
    
    return {"yaml_string": yaml_str}

def ask_human_node(state: AgentState) -> dict:
    """
    Placeholder node: In a real app, this pauses the graph to ask the user 
    for the missing fields identified in state['missing_attributes'].
    """
    print(f"\n[SYSTEM PAUSE] Missing required data: {state['missing_attributes']}")
    print("Routing to human for clarification...\n")
    return {} # No changes to state right now

def route_after_extraction(state: AgentState) -> str:
    """
    Conditional edge logic: Checks for missing attributes.
    """
    if len(state["missing_attributes"]) > 0:
        return "ask_human"
    
    return "yaml_compiler"

# GRAPH ASSEMBLY & COMPILATION

workflow = StateGraph(AgentState)

# 1. Add all nodes to the registry
workflow.add_node("unified_extractor", unified_extractor_node)
workflow.add_node("yaml_compiler", yaml_compiler_node)
workflow.add_node("ask_human", ask_human_node)

# 2. Define the starting edge
workflow.add_edge(START, "unified_extractor")

# 3. Add the Conditional Edge (The Router)
workflow.add_conditional_edges(
    "unified_extractor",      # The node we are coming from
    route_after_extraction,   # The routing function to evaluate
    {
        # Map the function's string output to the actual node names
        "yaml_compiler": "yaml_compiler",
        "ask_human": "ask_human"
    }
)

# 4. Define the endpoints
workflow.add_edge("yaml_compiler", END)
workflow.add_edge("ask_human", END)

app = workflow.compile()
# EXECUTION PASS
if __name__ == "__main__":
    from datetime import date
    
    # Ensure your API key is set!
    # os.environ["GEMINI_API_KEY"] = "your-api-key-here"
    
    # ---------------------------------------------------------
    # SCENARIO 1: PERFECT INPUT
    # ---------------------------------------------------------
    today = date.today().isoformat()
    sample_input_success = {
        "user_input": f"Hey, I just deployed Ghost version 22.5.2 today ({today}). Please add a changelog entry under my name, Richard Rodgers, noting that we updated the r77 block."
    }
    
    print("====================================================")
    print(" SCENARIO 1: PERFECT INPUT (Should generate YAML)")
    print("====================================================")
    for event in app.stream(sample_input_success, stream_mode="updates"):
        for node_name, state_update in event.items():
            print(f"\n🚀 NODE FINISHED: {node_name}")
            
            # If the YAML compiler ran, print the final output
            if "yaml_string" in state_update:
                print("\n✅ FINAL COMPILED YAML:")
                print(state_update["yaml_string"])


    # ---------------------------------------------------------
    # SCENARIO 2: BROKEN INPUT
    # ---------------------------------------------------------
    sample_input_missing = {
        "user_input": "Add a changelog entry for updating r77."
    }
    
    print("\n\n====================================================")
    print(" SCENARIO 2: BROKEN INPUT (Should route to Human)")
    print("====================================================")
    for event in app.stream(sample_input_missing, stream_mode="updates"):
        for node_name, state_update in event.items():
            print(f"\n🚀 NODE FINISHED: {node_name}")
            
            # If the extractor caught missing fields, print them
            if "missing_attributes" in state_update and state_update["missing_attributes"]:
                print(f"⚠️  Missing data flagged: {state_update['missing_attributes']}")

