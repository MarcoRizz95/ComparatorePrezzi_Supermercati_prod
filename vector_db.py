import streamlit as st
from pinecone import Pinecone
from sentence_transformers import SentenceTransformer
import time

class VectorDB:
    def __init__(self, api_key, index_name="scontrini-db"):
        # 1. Inizializza Pinecone
        self.pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        self.index = self.pc.Index(index_name)
        
        # 2. Carica il modello di Embedding (con Cache di Streamlit per velocità)
        self.model = self._load_model()

    @staticmethod
    @st.cache_resource(show_spinner=False)
    def _load_model():
        # Scarica il modello una sola volta all'avvio dell'app
        return SentenceTransformer('all-MiniLM-L6-v2')

    def get_embedding(self, text):
        """Converte il testo in una lista di 384 numeri"""
        if not text:
            return None
        # Genera il vettore e lo converte in lista standard Python
        return self.model.encode(text).tolist()

    def search_product(self, product_description, threshold=0.80):
        """
        Cerca se il prodotto esiste già nel DB.
        Ritorna il risultato migliore se supera la soglia di somiglianza.
        """
        vector = self.get_embedding(product_description)
        
        # Esegue la ricerca su Pinecone
        results = self.index.query(
            vector=vector,
            top_k=1,
            include_metadata=True
        )

        # Controlla se abbiamo trovato qualcosa
        if results['matches']:
            match = results['matches'][0]
            score = match['score']
            
            # Se la somiglianza è alta (es. > 80%), ci fidiamo
            if score >= threshold:
                return {
                    "found": True,
                    "score": score,
                    "category": match['metadata'].get('category'),
                    "normalized_name": match['metadata'].get('normalized_name')
                }
        
        return {"found": False, "score": 0}

    def add_product(self, raw_name, normalized_name, category):
        """Salva un nuovo prodotto nel database per il futuro"""
        vector = self.get_embedding(raw_name)
        
        # Creiamo un ID unico basato sul tempo o sul nome (semplificato qui)
        unique_id = f"{int(time.time())}_{raw_name[:10].strip()}"
        
        # Upsert su Pinecone
        self.index.upsert(
            vectors=[{
                "id": unique_id,
                "values": vector,
                "metadata": {
                    "original_text": raw_name,
                    "normalized_name": normalized_name,
                    "category": category
                }
            }]
        )
        return True
