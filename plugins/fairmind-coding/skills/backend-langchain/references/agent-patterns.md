# Agent Patterns Reference

## LangGraph Basics

### Simple Agent

```python
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from typing import TypedDict, Annotated
from operator import add

class AgentState(TypedDict):
    messages: Annotated[list, add]
    next_step: str

llm = ChatOpenAI(model="gpt-4o")

def call_model(state: AgentState) -> dict:
    messages = state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}

def should_continue(state: AgentState) -> str:
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tools"
    return END

# Build graph
workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_executor)

workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

app = workflow.compile()

# Run
result = await app.ainvoke({"messages": [HumanMessage(content="Hello")]})
```

## Tool Definition

### Basic Tools

```python
from langchain_core.tools import tool
from pydantic import BaseModel, Field

@tool
def search(query: str) -> str:
    """Search the web for information."""
    # Implementation
    return f"Results for: {query}"

@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression."""
    try:
        result = eval(expression)
        return str(result)
    except Exception as e:
        return f"Error: {e}"

# With schema
class SearchInput(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=5, description="Maximum results")

@tool(args_schema=SearchInput)
def advanced_search(query: str, max_results: int = 5) -> str:
    """Search with configurable results."""
    return f"Found {max_results} results for: {query}"
```

### Async Tools

```python
@tool
async def async_search(query: str) -> str:
    """Async search implementation."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.example.com/search?q={query}") as resp:
            data = await resp.json()
            return str(data)
```

### Tool Error Handling

```python
from langchain_core.tools import ToolException

@tool
def risky_operation(data: str) -> str:
    """Operation that might fail."""
    try:
        result = process_data(data)
        return result
    except ValueError as e:
        raise ToolException(f"Invalid data: {e}")

# Handle gracefully
tool_with_handling = risky_operation.with_fallbacks(
    [fallback_tool],
    exceptions_to_handle=(ToolException,),
)
```

## ReAct Agent

```python
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o")
tools = [search, calculator]

# Create ReAct agent
agent = create_react_agent(llm, tools)

# Run
result = await agent.ainvoke({
    "messages": [HumanMessage(content="What is 2 + 2 multiplied by the current year?")]
})
```

## Multi-Agent System

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Literal

class TeamState(TypedDict):
    messages: list
    next_agent: str
    task_complete: bool

def create_agent(name: str, system_prompt: str, tools: list):
    """Create a specialized agent."""
    llm_with_tools = llm.bind_tools(tools)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="messages"),
    ])

    return prompt | llm_with_tools

# Specialized agents
researcher = create_agent(
    "Researcher",
    "You research topics and gather information.",
    [search_tool],
)

writer = create_agent(
    "Writer",
    "You write content based on research.",
    [write_tool],
)

reviewer = create_agent(
    "Reviewer",
    "You review and improve content.",
    [],
)

# Supervisor
def supervisor(state: TeamState) -> dict:
    """Decide which agent should act next."""
    messages = state["messages"]

    supervisor_prompt = """
    You are a team supervisor. Based on the conversation, decide who should act next:
    - researcher: For gathering information
    - writer: For creating content
    - reviewer: For reviewing work
    - FINISH: If the task is complete

    Respond with only the agent name or FINISH.
    """

    response = llm.invoke([
        SystemMessage(content=supervisor_prompt),
        *messages,
    ])

    return {"next_agent": response.content.strip().lower()}

# Build graph
workflow = StateGraph(TeamState)
workflow.add_node("supervisor", supervisor)
workflow.add_node("researcher", researcher)
workflow.add_node("writer", writer)
workflow.add_node("reviewer", reviewer)

workflow.set_entry_point("supervisor")
workflow.add_conditional_edges(
    "supervisor",
    lambda x: x["next_agent"],
    {
        "researcher": "researcher",
        "writer": "writer",
        "reviewer": "reviewer",
        "finish": END,
    },
)

for agent in ["researcher", "writer", "reviewer"]:
    workflow.add_edge(agent, "supervisor")

app = workflow.compile()
```

## Human-in-the-Loop

```python
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph

# Add checkpointing for interrupts
memory = SqliteSaver.from_conn_string(":memory:")

def human_approval_needed(state: AgentState) -> str:
    """Check if human approval is needed."""
    last_message = state["messages"][-1]
    if "dangerous" in str(last_message.content).lower():
        return "wait_for_human"
    return "continue"

workflow = StateGraph(AgentState)
# ... add nodes ...

workflow.add_conditional_edges(
    "agent",
    human_approval_needed,
    {"wait_for_human": "human_review", "continue": "execute"},
)

app = workflow.compile(checkpointer=memory, interrupt_before=["human_review"])

# Run until interrupt
config = {"configurable": {"thread_id": "1"}}
result = await app.ainvoke({"messages": [HumanMessage("Do something dangerous")]}, config)

# Resume after human review
result = await app.ainvoke(None, config)  # Continues from checkpoint
```

## Streaming Agent Output

```python
async def stream_agent():
    async for event in agent.astream_events(
        {"messages": [HumanMessage("Research AI trends")]},
        version="v2",
    ):
        kind = event["event"]

        if kind == "on_chat_model_stream":
            content = event["data"]["chunk"].content
            if content:
                print(content, end="", flush=True)

        elif kind == "on_tool_start":
            print(f"\n🔧 Using tool: {event['name']}")

        elif kind == "on_tool_end":
            print(f"✅ Tool result: {event['data']['output'][:100]}...")
```

## Agent Memory

```python
from langgraph.checkpoint.sqlite import SqliteSaver

memory = SqliteSaver.from_conn_string("agent_memory.db")

agent_with_memory = create_react_agent(
    llm,
    tools,
    checkpointer=memory,
)

# Each thread maintains its own conversation
config = {"configurable": {"thread_id": "user_123"}}

# First message
result1 = await agent_with_memory.ainvoke(
    {"messages": [HumanMessage("My name is Alice")]},
    config,
)

# Follow-up (remembers context)
result2 = await agent_with_memory.ainvoke(
    {"messages": [HumanMessage("What's my name?")]},
    config,
)
```

## Tool Calling Pattern

```python
from langchain_core.messages import ToolMessage

def execute_tools(state: AgentState) -> dict:
    """Execute tool calls and return results."""
    last_message = state["messages"][-1]
    results = []

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]

        # Find and execute tool
        tool = next(t for t in tools if t.name == tool_name)
        result = tool.invoke(tool_args)

        results.append(
            ToolMessage(
                content=str(result),
                tool_call_id=tool_call["id"],
            )
        )

    return {"messages": results}
```

## Parallel Tool Execution

```python
import asyncio

async def execute_tools_parallel(state: AgentState) -> dict:
    """Execute multiple tool calls in parallel."""
    last_message = state["messages"][-1]

    async def run_tool(tool_call):
        tool = next(t for t in tools if t.name == tool_call["name"])
        result = await tool.ainvoke(tool_call["args"])
        return ToolMessage(
            content=str(result),
            tool_call_id=tool_call["id"],
        )

    results = await asyncio.gather(*[
        run_tool(tc) for tc in last_message.tool_calls
    ])

    return {"messages": list(results)}
```
