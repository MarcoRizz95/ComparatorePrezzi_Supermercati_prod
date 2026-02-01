import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re
import requests
# Usiamo la funzione specifica della libreria
from streamlit_js_eval import get_geolocation

# ... (Le tue funzioni clean_piva, clean_price e get_col_name rimangono identiche) ...

# --- CONNESSIONE (Invariata) ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    google_info = dict(st.secrets)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    lista_negozi_raw = ws_negozi.get_all_records()
    model = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    st.error(f"Errore connessione: {e}")
    st.stop()

st.title("🛍️ Spesa Smart & GPS")

tab_carica, tab_cerca = st.tabs(["📷 CARICA", "🔍 CERCA"])

# --- TAB CARICA (Invariato) ---
with tab_carica:
    # ... (Codice di caricamento scontrini) ...
    st.info("Sezione caricamento attiva.")

# --- TAB CERCA (Logica GPS migliorata) ---
with tab_cerca:
    st.subheader("🔍 Ricerca Prezzi e Distanza")
    
    # Inizializziamo le coordinate nella sessione
    if 'my_lat' not in st.session_state:
        st.session_state.my_lat = None
        st.session_state.my_lon = None

    # Pulsante esplicito per attivare il GPS
    if st.button("📍 Attiva/Aggiorna la mia posizione"):
        with st.spinner("Richiesta GPS in corso..."):
            loc = get_geolocation()
            if loc and 'coords' in loc:
                st.session_state.my_lat = loc['coords']['latitude']
                st.session_state.my_lon = loc['coords']['longitude']
                st.success(f"Posizione acquisita: {st.session_state.my_lat}, {st.session_state.my_lon}")
            else:
                st.error("Impossibile ottenere la posizione. Verifica che il sito abbia i permessi GPS nel browser.")

    # Visualizziamo lo stato attuale
    if st.session_state.my_lat:
        st.write(f"✅ GPS Attivo (Km calcolati su strada)")
    else:
        st.warning("Distanze non disponibili. Clicca il tasto sopra.")

    query = st.text_input("Cosa cerchi?", key="search_query_v26").upper().strip()
    
    if query:
        all_data = worksheet.get_all_records()
        if all_data:
            df_all = pd.DataFrame(all_data)
            df_all.columns = [str(c).strip() for c in df_all.columns]
            
            c_prod = get_col_name(df_all, 'PRODOTTO')
            c_indirizzo = get_col_name(df_all, 'INDIRIZZO')
            c_super = get_col_name(df_all, 'SUPERMERCATO')
            c_prezzo = get_col_name(df_all, 'NETTO') or get_col_name(df_all, 'UNITARIO')
            c_data = get_col_name(df_all, 'DATA')

            mask = df_all[c_prod].astype(str).str.contains(query, na=False)
            res = df_all[mask].copy()
            
            if not res.empty:
                res[c_prezzo] = res[c_prezzo].apply(clean_price)
                
                def add_road_dist(row):
                    if not st.session_state.my_lat:
                        return 999
                    
                    addr_to_find = re.sub(r'\W+', '', str(row[c_indirizzo])).upper()
                    neg = next((n for n in lista_negozi_raw if re.sub(r'\W+', '', str(n.get('Indirizzo_Standard (Pulito)', ''))).upper() == addr_to_find), None)
                    
                    if neg:
                        try:
                            t_lat = float(str(neg.get('Latitudine')).replace(',', '.'))
                            t_lon = float(str(neg.get('Longitudine')).replace(',', '.'))
                            return get_road_distance(st.session_state.my_lat, st.session_state.my_lon, t_lat, t_lon)
                        except: return 888
                    return 999

                res['KM'] = res.apply(add_road_dist, axis=1)
                res['dt'] = pd.to_datetime(res[c_data], format='%d/%m/%Y', errors='coerce')
                res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=[c_super, c_indirizzo])
                res = res.sort_values(by=c_prezzo)
                
                best = res.iloc[0]
                st.info(f"🏆 **{best[c_super]}** è il più economico per **{query}**")
                
                disp = res[[c_prezzo, 'KM', c_super, c_indirizzo, c_data]]
                disp.columns = ['€ Prezzo', 'Km Strada', 'Negozio', 'Indirizzo', 'Data']
                # Ordiniamo per prezzo come default
                st.dataframe(disp.sort_values(by='€ Prezzo'), use_container_width=True, hide_index=True)
            else:
                st.warning("Nessun prodotto trovato.")
