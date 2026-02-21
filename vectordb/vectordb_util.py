import chromadb
import os
import onnxruntime
import numpy as np
from transformers import AutoTokenizer
from langchain_chroma import Chroma
from langchain_core.documents import Document
from typing import List, Dict, Any, Optional
from core.db import SessionLocal
from core.models import Restaurant
from core.config import CHROMA_HOST, CHROMA_PORT
from sqlalchemy.orm import Session, joinedload

# ChromaDB 컬렉션 설정
COLLECTION_NAME_OHAENG = "ohaeng_rules_knowledge_base"
COLLECTION_NAME_RESTAURANTS = "restaurants_knowledge_base"
COLLECTION_NAME_MENUS = "menu_ohaeng_assignments"

# 임베딩 모델 설정
ONNX_MODEL_DIR = "/app/kure-v1-onnx"

embeddings: Optional['QuantizedEmbeddings'] = None
chroma_client: Optional[chromadb.HttpClient] = None

class QuantizedEmbeddings:
    def __init__(self, model_dir: str):
        onnx_path = os.path.join(model_dir, "quantized_model.onnx")
        
        if not os.path.exists(onnx_path):
            reason = f"ONNX model file not found at {onnx_path}. Please run 'convert_to_onnx.py' first."
            print(f"[Check File | QuantizedEmbeddings | REJECTED: {reason}]")
            raise FileNotFoundError(reason)

        print(f"[Load Model | QuantizedEmbeddings | INFO: Loading ONNX model from {onnx_path}]")
        self.session = onnxruntime.InferenceSession(onnx_path)
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        print(f"[Initialize | QuantizedEmbeddings | SUCCESS: Model and Tokenizer loaded]")

    def embed_query(self, text: str) -> List[float]:
        inputs = self.tokenizer(text, padding=True, truncation=True, return_tensors="np")
        input_feed = {'input_ids': inputs['input_ids'], 'attention_mask': inputs['attention_mask']}
        outputs = self.session.run(output_names=['last_hidden_state'], input_feed=input_feed)
        
        last_hidden_state = outputs[0]
        input_mask_expanded = inputs['attention_mask'][:, :, np.newaxis].astype(last_hidden_state.dtype)
        sum_embeddings = (last_hidden_state * input_mask_expanded).sum(axis=1)
        sum_mask = input_mask_expanded.sum(axis=1).clip(min=1e-9)
        sentence_embedding = sum_embeddings / sum_mask
        
        norm = np.linalg.norm(sentence_embedding, axis=1, keepdims=True)
        return (sentence_embedding / norm)[0].tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        inputs = self.tokenizer(texts, padding=True, truncation=True, return_tensors="np")
        input_feed = {'input_ids': inputs['input_ids'], 'attention_mask': inputs['attention_mask']}
        outputs = self.session.run(output_names=['last_hidden_state'], input_feed=input_feed)
        
        last_hidden_state = outputs[0]
        input_mask_expanded = inputs['attention_mask'][:, :, np.newaxis].astype(last_hidden_state.dtype)
        sum_embeddings = (last_hidden_state * input_mask_expanded).sum(axis=1)
        sum_mask = input_mask_expanded.sum(axis=1).clip(min=1e-9)
        sentence_embedding = sum_embeddings / sum_mask
        
        norm = np.linalg.norm(sentence_embedding, axis=1, keepdims=True)
        return (sentence_embedding / norm).tolist()

def get_embeddings() -> 'QuantizedEmbeddings':
    global embeddings
    if embeddings is None:
        embeddings = QuantizedEmbeddings(model_dir=ONNX_MODEL_DIR)
        try:
            test_embedding = embeddings.embed_query('테스트')
            print(f"[Test Embedding | get_embeddings | SUCCESS: Vector dimension {len(test_embedding)} verified]")
        except Exception as e:
            reason = f"Embedding test failed due to: {str(e)}"
            print(f"[Test Embedding | get_embeddings | REJECTED: {reason}]")
            raise RuntimeError(reason)
    return embeddings

def get_chroma_client() -> chromadb.HttpClient:
    global chroma_client
    if chroma_client is None:
        print(f"[Connect DB | ChromaClient | INFO: Connecting to {CHROMA_HOST}:{CHROMA_PORT}]")
        try:
            chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            print(f"[Connect DB | ChromaClient | SUCCESS: Connection established]")
        except Exception as e:
            reason = f"Could not connect to ChromaDB server at {CHROMA_HOST}:{CHROMA_PORT}. Details: {str(e)}"
            print(f"[Connect DB | ChromaClient | REJECTED: {reason}]")
            raise RuntimeError(reason)
    return chroma_client

def get_chroma_client_and_collection(collection_name: str, use_langchain_chroma: bool = False) -> tuple[chromadb.HttpClient, Any] | tuple[None, None]:
    try:
        client = get_chroma_client()
        if use_langchain_chroma:
            collection_obj = Chroma(client=client, collection_name=collection_name, embedding_function=get_embeddings())
            return client, collection_obj
        else:
            return client, client.get_collection(name=collection_name)
    except Exception as e:
        print(f"[Load Collection | ChromaUtil | REJECTED: Failed to load {collection_name} - {str(e)}]")
        return None, None

def fetch_and_create_document(restaurant_id: int, db: Session) -> Optional[Document]:
    try:
        restaurant_record = db.query(Restaurant).options(joinedload(Restaurant.menus)).filter(Restaurant.id == restaurant_id).one_or_none()
        
        if not restaurant_record:
            print(f"[Fetch Record | MySQL | REJECTED: Restaurant ID {restaurant_id} not found in database]")
            return None

        menu_names = [menu.menu_name for menu in restaurant_record.menus if menu.menu_name]
        combined_menus = ", ".join(menu_names) if menu_names else "메뉴 정보 없음"
        content = f"식당 이름: {restaurant_record.name}. 카테고리: {restaurant_record.category}. 주소: {restaurant_record.address}. 메뉴: {combined_menus}."
        
        return Document(
            page_content=content,
            metadata={"restaurant_id": restaurant_record.id, "name": restaurant_record.name, "category": restaurant_record.category, "address": restaurant_record.address, "source": "restaurant_db_restored"}
        )
    except Exception as e:
        print(f"[Fetch Record | MySQL | REJECTED: Exception occurred during DB query - {str(e)}]")
        return None

def restore_restaurant_data(target_id: int):    
    db: Optional[Session] = None
    try:
        client, vectorstore_restaurants = get_chroma_client_and_collection(COLLECTION_NAME_RESTAURANTS, use_langchain_chroma=True)
        if not client: return

        pre_op_count = vectorstore_restaurants._collection.count()
        print(f"[Load Store | ChromaDB | SUCCESS: Restaurant collection loaded with {pre_op_count} documents]")
        
        vectorstore_restaurants._collection.delete(where={"restaurant_id": target_id})
        post_delete_count = vectorstore_restaurants._collection.count()
        deleted_count = pre_op_count - post_delete_count
        print(f"[Delete Data | ChromaDB | SUCCESS: Deleted {deleted_count} documents for ID {target_id}]")
        
        db = SessionLocal() 
        document_to_restore = fetch_and_create_document(target_id, db)
        
        if not document_to_restore:
            print(f"[Restore Data | TaskHandler | REJECTED: Aborting restore for ID {target_id} - Document generation failed]")
            return 
        
        vectorstore_restaurants.add_documents(documents=[document_to_restore])
        final_count = vectorstore_restaurants._collection.count()
        print(f"[Insert Data | ChromaDB | SUCCESS: Re-saved ID {target_id}. Total count: {final_count}]")
        
        target_data = vectorstore_restaurants._collection.get(where={"restaurant_id": target_id}, include=["documents", "metadatas"])
        if target_data['documents']:
            print(f"[Verify Restore | TaskHandler | SUCCESS: Found restored data for {target_data['metadatas'][0].get('name')}]")
        else:
            print(f"[Verify Restore | TaskHandler | REJECTED: Could not find ID {target_id} after re-insertion attempt]")
            
    except Exception as e:
        print(f"[Restore Data | TaskHandler | REJECTED: Unexpected error during restoration - {str(e)}]")
    finally:
        if db: db.close()

def delete_restaurant_data_batch(target_ids: List[int]):
    client, collection = get_chroma_client_and_collection(COLLECTION_NAME_RESTAURANTS, use_langchain_chroma=False)
    if not client: return
    
    pre_op_count = collection.count()
    collection.delete(where={"restaurant_id": {"$in": target_ids}})
    post_delete_count = collection.count()
    
    print(f"[Batch Delete | ChromaDB | SUCCESS: Removed {pre_op_count - post_delete_count} documents. Remaining: {post_delete_count}]")

def check_restaurant_document(target_id: int):
    client, collection = get_chroma_client_and_collection(COLLECTION_NAME_RESTAURANTS, use_langchain_chroma=False)
    if not client: return 
    
    existing_documents = collection.get(where={"restaurant_id": target_id}, include=["metadatas", "documents"])
    found_count = len(existing_documents.get('ids', []))
        
    if found_count > 0:
        print(f"[Check Data | ChromaDB | SUCCESS: Found {found_count} documents for ID {target_id}]")
        for i in range(found_count):
            print(f"  - Document {i+1}: {existing_documents['metadatas'][i].get('name')} (ID: {existing_documents['ids'][i]})")
    else:
        print(f"[Check Data | ChromaDB | REJECTED: No documents found for ID {target_id}]")

def display_raw_collection_data(chroma_client: chromadb.HttpClient, collection_name: str, limit: int):
    try:
        collection = chroma_client.get_collection(name=collection_name)
        results = collection.get(limit=limit, include=['metadatas', 'documents', 'embeddings'])
        doc_count = len(results.get('ids', []))
        
        if doc_count == 0:
            print(f"[Inspect Collection | {collection_name} | INFO: Collection is empty]")
            return
        
        print(f"[Inspect Collection | {collection_name} | SUCCESS: Displaying {doc_count} of {collection.count()} documents]")
            
    except Exception as e:
        print(f"[Inspect Collection | {collection_name} | REJECTED: Failed to display data - {str(e)}]")

def check_all_collections():
    try:
        client = get_chroma_client()
        for col_name in [COLLECTION_NAME_RESTAURANTS, COLLECTION_NAME_MENUS, COLLECTION_NAME_OHAENG]:
            display_raw_collection_data(client, col_name, limit=50)
    except Exception as e:
        print(f"[Check All | ChromaUtil | REJECTED: Global check failed - {str(e)}]")

if __name__ == "__main__":
    try:
        get_chroma_client()
    except Exception:
        pass