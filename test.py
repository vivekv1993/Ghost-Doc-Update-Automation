import os
import dotenv
from langchain_core.messages import HumanMessage
from pprint import pprint
from langchain.chat_models import init_chat_model

dotenv.load_dotenv()

model = init_chat_model(
    "openrouter/free",
    model_provider="openai",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=1.0
)
messages = [HumanMessage("Hi , What model are you ? Answer in one line.")]
text = model.invoke(messages)
pprint(text.content)
