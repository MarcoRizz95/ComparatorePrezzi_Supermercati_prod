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
    """Calcola distanza stradale reale via OSRM"""
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        r = requests.get(url, timeout=5)
        data = r.json()
        if data['code'] == 'Ok':
            return round(data['routes'][0]['distance'] / 1000, 1)
    except: pass
    return None

def get_coords_from_address(address):
    """Trasforma un indirizzo scritto in coordinate Lat/Lon"""
    try:
        geolocator = Nominatim(user_agent="comparatore_prezzi_spesa")
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

# --- 3. INTERFACCIA ---
st.title("🛍️ Spesa Smart & Distanze")

tab_carica, tab_cerca = st.tabs(["📷 CARICA", "🔍 CERCA"])

# --- TAB CARICA (Invariato) ---
with tab_carica:
    if 'dati_analizzati' not in st.session_state: st.session_state.dati_analizzati = None
    files = st.file_uploader("Carica foto scontrino", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)
    if files:
        imgs = [ImageOps.exif_transpose(Image.open(f)) for f in files]
        if st.button("🚀 ANALIZZA"):
            with st.spinner("Analisi in corso..."):
                try:
                    all_db = worksheet.get_all_records()
                    glossario = list(set([str(r.get('Nome Standard Proposto', r.get('Proposta_Normalizzazione', ''))).upper() for r in all_db if r]))
                    prompt = f"""Analizza scontrino. Negozi conosciuti. Sconti: sottrai al prodotto sopra. Normalizzazione con: {glossario[:100]}. JSON richiesto."""
                    response = model.generate_content([prompt, *imgs])
                    st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                    st.rerun()
                except Exception as e: st.error(f"Errore: {e}")

    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi_raw if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo_letto', ''))).upper()
        with c3: data_f = st.text_input("Data", value="/".join(testata.get('data_iso', '2026-01-01').split("-")[::-1]))
        lista_edit = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Normalizzato": str(p.get('nome_standard', p.get('nome_letto', ''))).upper()} for p in d.get('prodotti', [])]
        edited_df = st.data_editor(pd.DataFrame(lista_edit), use_container_width=True, num_rows="dynamic", hide_index=True)
        if st.button("💾 SALVA"):
            final_rows = [[data_f, insegna_f, indirizzo_f, str(r['Prodotto']).upper(), clean_price(r['Prezzo Un.']) * float(r['Qtà']), 0, clean_price(r['Prezzo Un.']), r['Offerta'], r['Qtà'], "SI", str(r['Normalizzato']).upper()] for _, r in edited_df.iterrows()]
            worksheet.append_rows(final_rows)
            st.success("Salvataggio completato!"); st.session_state.dati_analizzati = None; st.rerun()

# --- TAB CERCA (Logica Fallback Geografica) ---
with tab_cerca:
    st.subheader("🔍 Dove costa meno?")
    
    if 'curr_lat' not in st.session_state: st.session_state.curr_lat = None

    # Sezione Posizione
    with st.expander("📍 Imposta la tua posizione", expanded=(st.session_state.curr_lat is None)):
        col_gps, col_manual = st.columns([1, 2])
        with col_gps:
            if st.button("Usa GPS"):
                loc = get_geolocation()
                if loc:
                    st.session_state.curr_lat = loc['coords']['latitude']
                    st.session_state.curr_lon = loc['coords']['longitude']
                    st.success("GPS Acquisito!")
        with col_manual:
            addr_input = st.text_input("Oppure scrivi dove sei (es: Verona, Via Roma)")
            if st.button("Imposta Indirizzo"):
                lat, lon = get_coords_from_address(addr_input)
                if lat:
                    st.session_state.curr_lat, st.session_state.curr_lon = lat, lon
                    st.success(f"Posizione impostata su {addr_input}")
                else: st.error("Indirizzo non trovato.")

    query = st.text_input("Cosa cerchi?", key="search_v27").upper().strip()
    
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
                    if not st.session_state.curr_lat: return 999
                    neg = next((n for n in lista_negozi_raw if re.sub(r'\W+', '', str(n.get('Indirizzo_Standard (Pulito)', ''))).upper() == re.sub(r'\W+', '', str(row[c_indirizzo])).upper()), None)
                    if neg:
                        try:
                            t_lat = float(str(neg.get('Latitudine')).replace(',', '.'))
                            t_lon = float(str(neg.get('Longitudine')).replace(',', '.'))
                            return get_road_distance(st.session_state.curr_lat, st.session_state.curr_lon, t_lat, t_lon)
                        except: return 888
                    return 999

                res['KM'] = res.apply(add_dist, axis=1)
                res['dt'] = pd.to_datetime(res[c_data], format='%d/%m/%Y', errors='coerce')
                res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=[c_super, c_indirizzo])
                res = res.sort_values(by=c_prezzo)
                
                best = res.iloc[0]
                st.info(f"🏆 **{best[c_super]}** è il più economico a **€{best[c_prezzo]:.2f}**")
                
                disp = res[[c_prezzo, 'KM', c_super, c_indirizzo, c_data]]
                disp.columns = ['€ Prezzo', 'Km Strada', 'Negozio', 'Indirizzo', 'Data']
                st.dataframe(disp.sort_values(by='€ Prezzo'), use_container_width=True, hide_index=True)
