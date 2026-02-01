import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re
import requests
from streamlit_js_eval import streamlit_js_eval

# --- 1. FUNZIONI DI SERVIZIO ---

def get_road_distance(lat1, lon1, lat2, lon2):
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        r = requests.get(url, timeout=5)
        data = r.json()
        if data['code'] == 'Ok':
            return round(data['routes'][0]['distance'] / 1000, 1)
    except: pass
    return None

def clean_piva(piva):
    solo_numeri = re.sub(r'\D', '', str(piva))
    return solo_numeri.zfill(11) if solo_numeri else ""

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

def get_col_name(df, keyword):
    for c in df.columns:
        if keyword.upper() in str(c).upper().strip():
            return c
    return None

# --- 2. CONNESSIONE ---
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

# --- 3. GPS UTENTE ---
# Aumentiamo la precisione della richiesta
user_pos = streamlit_js_eval(js_expressions="navigator.geolocation.getCurrentPosition(pos => pos.coords, err => console.log(err))", key="GPS_PRO_V1")
my_lat, my_lon = None, None
if user_pos and 'latitude' in user_pos:
    my_lat = user_pos['latitude']
    my_lon = user_pos['longitude']

st.title("🛍️ Spesa Smart & GPS")

tab_carica, tab_cerca = st.tabs(["📷 CARICA", "🔍 CERCA"])

# --- TAB CARICA (Invariato) ---
with tab_carica:
    st.info("Caricamento scontrini attivo.")
    # ... (Il codice di caricamento è lo stesso di V24) ...
    # (Per brevità ometto il blocco di upload, mantieni pure quello che hai)

# --- TAB CERCA (Con Diagnostica) ---
with tab_cerca:
    if my_lat:
        st.success(f"📍 GPS Attivo: Sei a coordinate {my_lat:.4f}, {my_lon:.4f}")
    else:
        st.warning("⚠️ Posizione GPS non ancora rilevata. Riprova a dare il consenso o ricarica la pagina.")

    query = st.text_input("Cosa cerchi?", key="sq").upper().strip()
    
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
                    # MATCH INDIRIZZO PIÙ ROBUSTO (Rimuoviamo spazi e punteggiatura)
                    addr_to_find = re.sub(r'\W+', '', str(row[c_indirizzo])).upper()
                    
                    neg = None
                    for n in lista_negozi_raw:
                        addr_anagrafe = re.sub(r'\W+', '', str(n.get('Indirizzo_Standard (Pulito)', ''))).upper()
                        if addr_anagrafe == addr_to_find:
                            neg = n
                            break
                    
                    if neg and my_lat:
                        try:
                            # Pulizia lat/lon da Sheet (gestisce sia virgola che punto)
                            target_lat = float(str(neg.get('Latitudine')).replace(',', '.'))
                            target_lon = float(str(neg.get('Longitudine')).replace(',', '.'))
                            return get_road_distance(my_lat, my_lon, target_lat, target_lon)
                        except: return 888 # Errore conversione numeri nello sheet
                    return 999 # Negozio non trovato o GPS mancante

                res['KM'] = res.apply(add_road_dist, axis=1)
                res['dt'] = pd.to_datetime(res[c_data], format='%d/%m/%Y', errors='coerce')
                res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=[c_super, c_indirizzo])
                res = res.sort_values(by=c_prezzo)
                
                best = res.iloc[0]
                km_label = f"{best['KM']} km" if best['KM'] < 800 else "N.D."
                st.info(f"🏆 Più economico: **{best[c_super]}** a **€{best[c_prezzo]:.2f}** ({km_label})")
                
                disp = res[[c_prezzo, 'KM', c_super, c_indirizzo, c_data]]
                disp.columns = ['€ Prezzo', 'Km Strada', 'Negozio', 'Indirizzo', 'Data']
                st.dataframe(disp, use_container_width=True, hide_index=True)
            else:
                st.warning("Nessun prodotto trovato.")
