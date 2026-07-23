# Memory Patterns Reference

## Conversation Memory Types

### Buffer Memory

```python
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import HumanMessage, AIMessage

# Simple buffer - keeps all messages
memory = ConversationBufferMemory(return_messages=True)

memory.save_context(
    {"input": "Hi, I'm Alice"},
    {"output": "Hello Alice! How can I help?"}
)

# Get history
history = memory.load_memory_variables({})
# {"history": [HumanMessage(...), AIMessage(...)]}
```

### Window Memory

```python
from langchain.memory import ConversationBufferWindowMemory

# Keep last k exchanges
memory = ConversationBufferWindowMemory(k=5, return_messages=True)

# Automatically drops older messages when window exceeded
```

### Summary Memory

```python
from langchain.memory import ConversationSummaryMemory

# Summarizes conversation to save tokens
memory = ConversationSummaryMemory(llm=llm, return_messages=True)

# As conversation grows, older parts are summarized
memory.save_context(
    {"input": "Tell me about quantum computing"},
    {"output": "Quantum computing uses quantum mechanical phenomena..."}
)

# Memory contains summary of past + recent messages
```

### Summary Buffer Memory

```python
from langchain.memory import ConversationSummaryBufferMemory

# Hybrid: summary for old, full buffer for recent
memory = ConversationSummaryBufferMemory(
    llm=llm,
    max_token_limit=500,  # Summarize when exceeds this
    return_messages=True,
)
```

### Entity Memory

```python
from langchain.memory import ConversationEntityMemory

# Tracks entities mentioned in conversation
memory = ConversationEntityMemory(llm=llm)

memory.save_context(
    {"input": "My dog's name is Max, he's a golden retriever"},
    {"output": "Max sounds like a wonderful golden retriever!"}
)

# Can query specific entities
entities = memory.entity_store.get("Max")
# Returns: "Max is a golden retriever, the user's dog"
```

## LangGraph Memory

### Checkpointer Memory

```python
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import MemorySaver

# In-memory (for development)
memory = MemorySaver()

# SQLite (persistent)
memory = SqliteSaver.from_conn_string("memory.db")

# Compile graph with checkpointer
app = workflow.compile(checkpointer=memory)

# Each thread maintains separate state
config = {"configurable": {"thread_id": "user_123"}}

# First message
result1 = await app.ainvoke(
    {"messages": [HumanMessage("I'm Alice")]},
    config,
)

# Follow-up (same thread)
result2 = await app.ainvoke(
    {"messages": [HumanMessage("What's my name?")]},
    config,
)
# AI knows the name from previous message
```

### PostgreSQL Checkpointer

```python
from langgraph.checkpoint.postgres import PostgresSaver

# Connection pool for production
async with AsyncConnectionPool(
    conninfo="postgresql://user:pass@localhost/db",
    max_size=20,
) as pool:
    memory = PostgresSaver(pool)

    # Setup tables
    await memory.setup()

    app = workflow.compile(checkpointer=memory)
```

## Implementing Custom Memory

### Basic Pattern

```python
from typing import List, Dict, Any
from langchain_core.messages import BaseMessage

class CustomMemory:
    def __init__(self, max_messages: int = 100):
        self.messages: List[BaseMessage] = []
        self.max_messages = max_messages
        self.metadata: Dict[str, Any] = {}

    def add_message(self, message: BaseMessage):
        self.messages.append(message)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def get_messages(self, limit: int = None) -> List[BaseMessage]:
        if limit:
            return self.messages[-limit:]
        return self.messages

    def clear(self):
        self.messages = []
        self.metadata = {}

    def set_metadata(self, key: str, value: Any):
        self.metadata[key] = value

    def get_metadata(self, key: str) -> Any:
        return self.metadata.get(key)
```

### Redis-Backed Memory

```python
import redis
import json
from langchain_core.messages import messages_from_dict, messages_to_dict

class RedisMemory:
    def __init__(self, redis_url: str, ttl: int = 3600):
        self.redis = redis.from_url(redis_url)
        self.ttl = ttl

    def _key(self, session_id: str) -> str:
        return f"chat:memory:{session_id}"

    def add_messages(self, session_id: str, messages: List[BaseMessage]):
        key = self._key(session_id)
        existing = self.get_messages(session_id)
        existing.extend(messages)

        self.redis.setex(
            key,
            self.ttl,
            json.dumps(messages_to_dict(existing))
        )

    def get_messages(self, session_id: str) -> List[BaseMessage]:
        key = self._key(session_id)
        data = self.redis.get(key)

        if not data:
            return []

        return messages_from_dict(json.loads(data))

    def clear(self, session_id: str):
        self.redis.delete(self._key(session_id))
```

## Memory in Chains

### With LCEL

```python
from langchain_core.runnables import RunnablePassthrough

def get_session_history(session_id: str) -> List[BaseMessage]:
    return memory.get_messages(session_id)

chain_with_memory = (
    RunnablePassthrough.assign(
        history=lambda x: get_session_history(x["session_id"])
    )
    | prompt  # Includes MessagesPlaceholder for history
    | llm
    | StrOutputParser()
)

# Usage
result = await chain_with_memory.ainvoke({
    "session_id": "user_123",
    "input": "Hello!",
})
```

### With RunnableWithMessageHistory

```python
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory

# Store for session histories
store = {}

def get_session_history(session_id: str) -> ChatMessageHistory:
    if session_id not in store:
        store[session_id] = ChatMessageHistory()
    return store[session_id]

chain_with_history = RunnableWithMessageHistory(
    prompt | llm | StrOutputParser(),
    get_session_history,
    input_messages_key="input",
    history_messages_key="history",
)

# Usage
result = await chain_with_history.ainvoke(
    {"input": "Hello!"},
    config={"configurable": {"session_id": "user_123"}},
)
```

## Memory Strategies

### Sliding Window with Summary

```python
class SlidingWindowSummaryMemory:
    def __init__(self, llm, window_size: int = 10, summary_threshold: int = 20):
        self.llm = llm
        self.window_size = window_size
        self.summary_threshold = summary_threshold
        self.messages: List[BaseMessage] = []
        self.summary: str = ""

    async def add_message(self, message: BaseMessage):
        self.messages.append(message)

        if len(self.messages) > self.summary_threshold:
            await self._summarize_old_messages()

    async def _summarize_old_messages(self):
        # Keep recent messages
        recent = self.messages[-self.window_size:]
        old = self.messages[:-self.window_size]

        # Summarize old messages
        summary_prompt = f"""
        Previous summary: {self.summary}

        New messages to incorporate:
        {self._format_messages(old)}

        Create an updated summary:
        """

        new_summary = await self.llm.ainvoke(summary_prompt)
        self.summary = new_summary.content
        self.messages = recent

    def get_context(self) -> str:
        return f"""
        Conversation Summary: {self.summary}

        Recent Messages:
        {self._format_messages(self.messages)}
        """
```

### Semantic Memory Retrieval

```python
class SemanticMemory:
    def __init__(self, embeddings, vectorstore):
        self.embeddings = embeddings
        self.vectorstore = vectorstore

    async def add_message(self, message: BaseMessage, metadata: dict = None):
        doc = Document(
            page_content=message.content,
            metadata={
                "type": message.type,
                "timestamp": datetime.now().isoformat(),
                **(metadata or {}),
            }
        )
        await self.vectorstore.aadd_documents([doc])

    async def get_relevant_history(
        self,
        query: str,
        k: int = 5
    ) -> List[Document]:
        return await self.vectorstore.asimilarity_search(query, k=k)
```

## Multi-User Memory

```python
class MultiUserMemory:
    def __init__(self, storage_backend):
        self.storage = storage_backend

    def _user_key(self, user_id: str, session_id: str) -> str:
        return f"{user_id}:{session_id}"

    async def get_user_sessions(self, user_id: str) -> List[str]:
        """Get all session IDs for a user."""
        return await self.storage.list_keys(prefix=f"{user_id}:")

    async def get_messages(
        self,
        user_id: str,
        session_id: str
    ) -> List[BaseMessage]:
        key = self._user_key(user_id, session_id)
        return await self.storage.get(key) or []

    async def clear_user_data(self, user_id: str):
        """GDPR compliance - delete all user data."""
        sessions = await self.get_user_sessions(user_id)
        for session in sessions:
            await self.storage.delete(session)
```
