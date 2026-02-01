import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re
import requests
from streamlit_js_eval import get_geolocation
from geopy.geocoders import Nominatim

# --- 1. FUNZIONI DI SERVIZIO ---

def get_road_distance(lat1, lon1, lat2, lon2):
    try:
        # Passato a HTTPS per sicurezza browser
        url = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        r = requests.get(url, timeout=5)
        data = r.json()
        if data['code'] == 'Ok':
            return round(data['routes'][0]['distance'] / 1000, 1)
    except Exception as e:
        print(f"Errore OSRM: {e}")
    return None

def get_coords_from_address(address):
    try:
        geolocator = Nominatim(user_agent="comparatore_spesa_v28")
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
    except: pass
    return None, None

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
        if keyword.upper() in str(c).upper().strip(): return c
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

st.title("🛍️ Spesa Smart & Distanze")

tab_carica, tab_cerca = st.tabs(["📷 CARICA", "🔍 CERCA"])

# --- TAB CARICA (Codice invariato per brevità) ---
with tab_carica:
    st.info("Trascina qui lo scontrino per analizzarlo.")
    # ... mantieni il blocco upload che hai già ...

# --- TAB CERCA (Logica con Debug) ---
with tab_cerca:
    st.subheader("🔍 Dove costa meno?")
    
    # Inizializzazione sessione
    if 'curr_lat' not in st.session_state: st.session_state.curr_lat = None
    if 'curr_lon' not in st.session_state: st.session_state.curr_lon = None

    # DIAGNOSTICA POSIZIONE
    if st.session_state.curr_lat:
        st.success(f"📍 Posizione Attuale: {st.session_state.curr_lat}, {st.session_state.curr_lon}")
    else:
        st.error("📍 Posizione NON impostata. Usa il box qui sotto.")

    with st.expander("⚙️ Impostazioni Posizione", expanded=(st.session_state.curr_lat is None)):
        c_gps, c_man = st.columns([1, 2])
        with c_gps:
            if st.button("Usa GPS"):
                loc = get_geolocation()
                if loc:
                    st.session_state.curr_lat = loc['coords']['latitude']
                    st.session_state.curr_lon = loc['coords']['longitude']
                    st.rerun()
        with c_man:
            addr_in = st.text_input("Inserisci città o indirizzo (es: Verona)")
            if st.button("Conferma Indirizzo"):
                lat, lon = get_coords_from_address(addr_in)
                if lat:
                    st.session_state.curr_lat, st.session_state.curr_lon = lat, lon
                    st.rerun()
                else: st.error("Indirizzo non trovato.")

    query = st.text_input("Cerca prodotto", key="s_v28").upper().strip()
    
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
                
                def add_dist(row):
                    # Se non abbiamo la tua posizione, non possiamo calcolare nulla
                    if not st.session_state.curr_lat: return 999
                    
                    # Pulizia stringa indirizzo per il confronto
                    def clean_str(s): return re.sub(r'\W+', '', str(s)).upper()
                    
                    addr_to_find = clean_str(row[c_indirizzo])
                    
                    # Cerchiamo nell'anagrafe
                    neg = None
                    for n in lista_negozi_raw:
                        # Confrontiamo l'indirizzo standard dell'anagrafe con quello dello scontrino
                        if clean_str(n.get('Indirizzo_Standard (Pulito)', '')) == addr_to_find:
                            neg = n
                            break
                    
                    if neg:
                        try:
                            # Proviamo a convertire le coordinate dello Sheet
                            t_lat = float(str(neg.get('Latitudine')).replace(',', '.'))
                            t_lon = float(str(neg.get('Longitudine')).replace(',', '.'))
                            km = get_road_distance(st.session_state.curr_lat, st.session_state.curr_lon, t_lat, t_lon)
                            return km if km is not None else 777 # Errore chiamata OSRM
                        except: return 888 # Errore conversione numeri Sheet
                    return 999 # Nessun match tra indirizzo scontrino e anagrafe

                res['KM'] = res.apply(add_dist, axis=1)
                res['dt'] = pd.to_datetime(res[c_data], format='%d/%m/%Y', errors='coerce')
                res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=[c_super, c_indirizzo])
                res = res.sort_values(by=c_prezzo)
                
                # Visualizzazione risultati
                st.dataframe(res[[c_prezzo, 'KM', c_super, c_indirizzo, c_data]], use_container_width=True, hide_index=True)
                
                # Messaggi di aiuto se vedi numeri strani
                if 999 in res['KM'].values:
                    st.info("ℹ️ Alcuni negozi mostrano '999' perché l'indirizzo nello scontrino non corrisponde esattamente a quello nell'Anagrafe_Negozi.")
            else: st.warning("Nessun prodotto trovato.")
