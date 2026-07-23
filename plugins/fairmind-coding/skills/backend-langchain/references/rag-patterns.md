# RAG Patterns Reference

## Document Loading

```python
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    DirectoryLoader,
    WebBaseLoader,
)

# Single PDF
loader = PyPDFLoader("document.pdf")
docs = loader.load()

# Directory of files
loader = DirectoryLoader(
    "./documents",
    glob="**/*.pdf",
    loader_cls=PyPDFLoader,
)
docs = loader.load()

# Web pages
loader = WebBaseLoader(["https://example.com/page1", "https://example.com/page2"])
docs = loader.load()

# With metadata
for doc in docs:
    doc.metadata["source_type"] = "pdf"
    doc.metadata["loaded_at"] = datetime.now().isoformat()
```

## Text Splitting

```python
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    TokenTextSplitter,
    MarkdownHeaderTextSplitter,
)

# Recursive character splitter (most common)
splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
    separators=["\n\n", "\n", " ", ""],
)
chunks = splitter.split_documents(docs)

# Token-based splitting
splitter = TokenTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    encoding_name="cl100k_base",  # GPT-4 encoding
)
chunks = splitter.split_documents(docs)

# Markdown-aware splitting
md_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]
)
md_chunks = md_splitter.split_text(markdown_text)
```

## Embeddings

```python
from langchain_openai import OpenAIEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings

# OpenAI embeddings
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    dimensions=1536,
)

# Local embeddings
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    model_kwargs={"device": "cpu"},
)

# Generate embeddings
vectors = embeddings.embed_documents(["text 1", "text 2"])
query_vector = embeddings.embed_query("search query")
```

## Vector Stores

### Chroma

```python
from langchain_community.vectorstores import Chroma

# Create from documents
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db",
    collection_name="my_collection",
)

# Load existing
vectorstore = Chroma(
    persist_directory="./chroma_db",
    embedding_function=embeddings,
    collection_name="my_collection",
)

# Add documents
vectorstore.add_documents(new_docs)

# Delete
vectorstore.delete(ids=["doc1", "doc2"])
```

### Pinecone

```python
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone

pc = Pinecone(api_key="...")
index = pc.Index("my-index")

vectorstore = PineconeVectorStore(
    index=index,
    embedding=embeddings,
    text_key="text",
)

# Upsert with metadata
vectorstore.add_documents(
    documents=chunks,
    ids=[f"doc_{i}" for i in range(len(chunks))],
)
```

### FAISS

```python
from langchain_community.vectorstores import FAISS

# Create
vectorstore = FAISS.from_documents(chunks, embeddings)

# Save/Load
vectorstore.save_local("faiss_index")
vectorstore = FAISS.load_local("faiss_index", embeddings)

# Merge indexes
vectorstore.merge_from(other_vectorstore)
```

## Retrieval Strategies

### Basic Retrieval

```python
# Similarity search
retriever = vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 4},
)

# MMR (Maximum Marginal Relevance) for diversity
retriever = vectorstore.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 4, "fetch_k": 20, "lambda_mult": 0.5},
)

# With score threshold
retriever = vectorstore.as_retriever(
    search_type="similarity_score_threshold",
    search_kwargs={"score_threshold": 0.8, "k": 4},
)
```

### Multi-Query Retrieval

```python
from langchain.retrievers.multi_query import MultiQueryRetriever

retriever = MultiQueryRetriever.from_llm(
    retriever=vectorstore.as_retriever(),
    llm=llm,
)

# Generates multiple query variations
docs = retriever.invoke("What is the return policy?")
```

### Contextual Compression

```python
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor

compressor = LLMChainExtractor.from_llm(llm)

retriever = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=vectorstore.as_retriever(),
)

# Returns compressed, relevant excerpts
docs = retriever.invoke("specific question")
```

### Ensemble Retrieval

```python
from langchain.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever

# Keyword-based retriever
bm25_retriever = BM25Retriever.from_documents(chunks)
bm25_retriever.k = 4

# Vector retriever
vector_retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

# Combine with weights
ensemble_retriever = EnsembleRetriever(
    retrievers=[bm25_retriever, vector_retriever],
    weights=[0.3, 0.7],
)
```

### Parent Document Retrieval

```python
from langchain.retrievers import ParentDocumentRetriever
from langchain.storage import InMemoryStore

# Store for full documents
docstore = InMemoryStore()

# Create retriever
retriever = ParentDocumentRetriever(
    vectorstore=vectorstore,
    docstore=docstore,
    child_splitter=RecursiveCharacterTextSplitter(chunk_size=400),
    parent_splitter=RecursiveCharacterTextSplitter(chunk_size=2000),
)

# Add documents
retriever.add_documents(docs)

# Search returns parent documents
results = retriever.invoke("query")
```

## RAG Chain Patterns

### Basic RAG

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

prompt = ChatPromptTemplate.from_template("""
Answer the question based only on the following context:

{context}

Question: {question}
""")

rag_chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)
```

### RAG with Sources

```python
from langchain_core.runnables import RunnableParallel

def format_docs_with_sources(docs):
    formatted = []
    for i, doc in enumerate(docs):
        formatted.append(f"[{i+1}] {doc.page_content}")
    return "\n\n".join(formatted)

rag_chain_with_sources = (
    RunnableParallel(
        context=retriever,
        question=RunnablePassthrough(),
    )
    | RunnablePassthrough.assign(
        formatted_context=lambda x: format_docs_with_sources(x["context"])
    )
    | {
        "answer": prompt | llm | StrOutputParser(),
        "sources": lambda x: [doc.metadata.get("source") for doc in x["context"]],
    }
)
```

### Conversational RAG

```python
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

# Contextualize question based on chat history
contextualize_prompt = ChatPromptTemplate.from_messages([
    ("system", "Given the chat history and latest question, reformulate the question to be standalone."),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{question}"),
])

contextualize_chain = contextualize_prompt | llm | StrOutputParser()

def contextualize_question(input_dict):
    if input_dict.get("chat_history"):
        return contextualize_chain.invoke(input_dict)
    return input_dict["question"]

# Full conversational RAG
rag_prompt = ChatPromptTemplate.from_messages([
    ("system", "Answer based on context:\n\n{context}"),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{question}"),
])

conversational_rag = (
    RunnablePassthrough.assign(
        standalone_question=contextualize_question
    )
    | RunnablePassthrough.assign(
        context=lambda x: retriever.invoke(x["standalone_question"])
    )
    | rag_prompt
    | llm
    | StrOutputParser()
)
```

### Self-Query Retrieval

```python
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain.chains.query_constructor.base import AttributeInfo

metadata_field_info = [
    AttributeInfo(
        name="category",
        description="The category of the document",
        type="string",
    ),
    AttributeInfo(
        name="date",
        description="The date the document was created",
        type="date",
    ),
]

retriever = SelfQueryRetriever.from_llm(
    llm=llm,
    vectorstore=vectorstore,
    document_contents="Product documentation",
    metadata_field_info=metadata_field_info,
)

# Automatically filters based on query
docs = retriever.invoke("What products were released in 2024?")
```

## Evaluation

```python
from langchain.evaluation import load_evaluator

# Answer relevancy
evaluator = load_evaluator("qa")
result = evaluator.evaluate_strings(
    prediction="The return policy is 30 days.",
    input="What is the return policy?",
    reference="30 days",
)

# Faithfulness (grounded in context)
evaluator = load_evaluator("context_qa")
result = evaluator.evaluate_strings(
    prediction="The policy is 30 days.",
    input="What is the return policy?",
    reference="Our return policy allows returns within 30 days.",
)
```
