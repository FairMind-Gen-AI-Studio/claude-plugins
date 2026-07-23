# Chain Patterns Reference

## LCEL Basics

### Simple Chain

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

llm = ChatOpenAI(model="gpt-4o")
prompt = ChatPromptTemplate.from_template("Tell me a joke about {topic}")
parser = StrOutputParser()

# Pipe operator creates chain
chain = prompt | llm | parser

# Invoke
result = chain.invoke({"topic": "programming"})

# Async invoke
result = await chain.ainvoke({"topic": "programming"})

# Stream
for chunk in chain.stream({"topic": "programming"}):
    print(chunk, end="")

# Batch
results = chain.batch([{"topic": "cats"}, {"topic": "dogs"}])
```

### RunnablePassthrough

```python
from langchain_core.runnables import RunnablePassthrough

# Pass input through unchanged
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | llm
)

# Assign additional values
chain = RunnablePassthrough.assign(
    context=lambda x: retriever.invoke(x["question"])
) | prompt | llm
```

### RunnableLambda

```python
from langchain_core.runnables import RunnableLambda

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

chain = (
    {"context": retriever | RunnableLambda(format_docs), "question": RunnablePassthrough()}
    | prompt
    | llm
)

# Async lambda
async def async_format(docs):
    return "\n\n".join(doc.page_content for doc in docs)

chain = retriever | RunnableLambda(async_format)
```

## Branching and Routing

### RunnableBranch

```python
from langchain_core.runnables import RunnableBranch

# Conditional routing
branch = RunnableBranch(
    (lambda x: "math" in x["question"].lower(), math_chain),
    (lambda x: "code" in x["question"].lower(), code_chain),
    default_chain,  # Fallback
)

chain = {"question": RunnablePassthrough()} | branch
```

### Router Chain

```python
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

router_prompt = PromptTemplate.from_template("""
Classify the question into one of: math, code, general

Question: {question}
Classification:""")

router_chain = router_prompt | llm | StrOutputParser()

def route(info):
    classification = info["classification"].strip().lower()
    if "math" in classification:
        return math_chain
    elif "code" in classification:
        return code_chain
    return general_chain

chain = (
    RunnablePassthrough.assign(classification=router_chain)
    | RunnableLambda(route)
)
```

## Parallel Execution

### RunnableParallel

```python
from langchain_core.runnables import RunnableParallel

# Execute multiple chains in parallel
parallel = RunnableParallel(
    summary=summary_chain,
    sentiment=sentiment_chain,
    keywords=keyword_chain,
)

result = parallel.invoke({"text": "Some long article..."})
# Returns: {"summary": "...", "sentiment": "...", "keywords": [...]}
```

### Map Over List

```python
chain = prompt | llm | parser

# Process list of inputs
results = chain.batch(
    [{"topic": t} for t in topics],
    config={"max_concurrency": 5}
)
```

## Structured Output

### Pydantic Parser

```python
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing import List

class Analysis(BaseModel):
    summary: str = Field(description="Brief summary")
    sentiment: str = Field(description="Overall sentiment")
    key_points: List[str] = Field(description="Main points")

parser = PydanticOutputParser(pydantic_object=Analysis)

prompt = ChatPromptTemplate.from_messages([
    ("system", "Analyze the text and respond in the specified format.\n{format_instructions}"),
    ("user", "{text}"),
])

chain = (
    {"text": RunnablePassthrough(), "format_instructions": lambda _: parser.get_format_instructions()}
    | prompt
    | llm
    | parser
)
```

### With Function Calling

```python
from langchain_core.utils.function_calling import convert_to_openai_function

class SearchQuery(BaseModel):
    query: str = Field(description="Search query")
    filters: dict = Field(default={}, description="Search filters")

llm_with_tools = llm.bind_functions([convert_to_openai_function(SearchQuery)])

chain = prompt | llm_with_tools
```

## Error Handling

### Fallbacks

```python
from langchain_openai import ChatOpenAI

primary_llm = ChatOpenAI(model="gpt-4o")
fallback_llm = ChatOpenAI(model="gpt-3.5-turbo")

# Use fallback if primary fails
chain = (prompt | primary_llm).with_fallbacks([prompt | fallback_llm])
```

### Retry Logic

```python
from langchain_core.runnables import RunnableConfig

chain_with_retry = chain.with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
)
```

## Caching

```python
from langchain_core.globals import set_llm_cache
from langchain_community.cache import SQLiteCache

# Enable caching
set_llm_cache(SQLiteCache(database_path=".langchain.db"))

# Or Redis
from langchain_community.cache import RedisCache
import redis

set_llm_cache(RedisCache(redis_=redis.Redis()))
```

## Streaming

### Token Streaming

```python
async for chunk in chain.astream({"question": "Tell me about AI"}):
    print(chunk.content, end="", flush=True)
```

### Event Streaming

```python
async for event in chain.astream_events({"question": "Hello"}, version="v2"):
    if event["event"] == "on_chat_model_stream":
        print(event["data"]["chunk"].content, end="")
    elif event["event"] == "on_tool_end":
        print(f"\nTool result: {event['data']['output']}")
```

## Callbacks

```python
from langchain_core.callbacks import BaseCallbackHandler

class LoggingHandler(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        print(f"LLM started with {len(prompts)} prompts")

    def on_llm_end(self, response, **kwargs):
        print(f"LLM finished")

    def on_chain_error(self, error, **kwargs):
        print(f"Chain error: {error}")

# Use callback
result = chain.invoke(
    {"question": "Hello"},
    config={"callbacks": [LoggingHandler()]}
)
```

## Configuration

```python
from langchain_core.runnables import ConfigurableField

# Make model configurable
configurable_llm = ChatOpenAI(model="gpt-4o").configurable_fields(
    temperature=ConfigurableField(
        id="temperature",
        name="Temperature",
        description="Controls randomness",
    )
)

chain = prompt | configurable_llm

# Use with different config
result = chain.invoke(
    {"topic": "science"},
    config={"configurable": {"temperature": 0.9}}
)
```

## Complete Example

```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_community.vectorstores import Chroma

# Setup
llm = ChatOpenAI(model="gpt-4o", temperature=0)
embeddings = OpenAIEmbeddings()
vectorstore = Chroma(embedding_function=embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

# Prompt
prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a helpful assistant. Use the context to answer questions.

Context: {context}"""),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])

# Format context
def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

# Chain
chain = (
    RunnablePassthrough.assign(
        context=lambda x: format_docs(retriever.invoke(x["question"]))
    )
    | prompt
    | llm
    | StrOutputParser()
)

# Usage
result = await chain.ainvoke({
    "question": "What is the return policy?",
    "history": [],
})
```
