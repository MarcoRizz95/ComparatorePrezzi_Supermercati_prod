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
        url = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        r = requests.get(url, timeout=5)
        data = r.json()
        if data['code'] == 'Ok':
            return round(data['routes'][0]['distance'] / 1000, 1)
    except: pass
    return None

def get_coords_from_address(address):
    try:
        geolocator = Nominatim(user_agent="comparatore_spesa_v30")
        location = geolocator.geocode(address)
        if location: return location.latitude, location.longitude
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

# --- 3. GESTIONE POSIZIONE NELLA SESSIONE ---
if 'my_lat' not in st.session_state: st.session_state.my_lat = None
if 'my_lon' not in st.session_state: st.session_state.my_lon = None

st.title("🛍️ Caricatore scontrini + Ricerca Prodotti & Distanze")

tab_carica, tab_cerca = st.tabs(["📷 CARICA SCONTRINO", "🔍 CERCA PREZZI"])

# --- TAB 1: CARICAMENTO ---
with tab_carica:
    if 'dati_analizzati' not in st.session_state: st.session_state.dati_analizzati = None
    files = st.file_uploader("Carica o scatta foto (anche multiple)", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)
    
    if files:
        imgs = [ImageOps.exif_transpose(Image.open(f)) for f in files]
        st.image(imgs, width=150)
        if st.button("🚀 ANALIZZA SCONTRINO"):
            with st.spinner("L'IA sta analizzando..."):
                try:
                    all_db = worksheet.get_all_records()
                    glossario = list(set([str(r.get('Proposta_Normalizzazione', r.get('Nome Standard Proposto', ''))).upper() for r in all_db if r]))
                    
                    # PROMPT V18 (IL TUO PREFERITO) + MULTI-FOTO
                    prompt = f"""
                    ATTENZIONE: Queste immagini sono parti dello STESSO scontrino. Analizzale insieme come un unico documento.
                    Agisci come un contabile esperto. Analizza lo scontrino con queste REGOLE FISSE:

                    1. SCONTI: Se vedi 'SCONTO', 'FIDATY', prezzi negativi (es: 1,50-S o -0,90) o sconti con "%" 
                       NON creare nuove righe. Sottrai il valore al prodotto sopra.
                       Esempio: riga 1 'MOZZARELLA 4.00' e riga 2 'SCONTO -1.00' = Mozzarella a 3.00 (is_offerta: SI).

                    2. MOLTIPLICAZIONI: Se vedi '2 x 1.50' sopra un prodotto, unitario 1.50 e quantita 2.

                    3. NORMALIZZAZIONE: Usa i nomi da questa lista se corrispondono: {glossario[:150]}

                    4. ESTREMA PRECISIONE: Non inventare prodotti e non raggrupparli. Ogni riga fisica va letta.

                    JSON:
                    {{
                      "testata": {{ "p_iva": "", "indirizzo_letto": "", "data_iso": "YYYY-MM-DD", "totale_scontrino_letto": 0.0 }},
                      "prodotti": [ {{ "nome_letto": "", "prezzo_unitario": 0.0, "quantita": 1, "is_offerta": "SI/NO", "nome_standard": "" }} ]
                    }}
                    """
                    response = model.generate_content([prompt, *imgs])
                    st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                    st.rerun()
                except Exception as e: st.error(f"Errore: {e}")

    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        prodotti_raw = d.get('prodotti', [])
        tot_calc = sum([clean_price(p.get('prezzo_unitario', 0)) * float(p.get('quantita', 1)) for p in prodotti_raw])
        st.info(f"💰 Somma articoli rilevati: **€{tot_calc:.2f}**")
        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi_raw if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo_letto', ''))).upper()
        with c3: 
            d_iso = testata.get('data_iso', '2026-01-01')
            data_f = st.text_input("Data", value="/".join(d_iso.split("-")[::-1]))
        lista_edit = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Normalizzato": str(p.get('nome_standard', p.get('nome_letto', ''))).upper()} for p in prodotti_raw]
        edited_df = st.data_editor(pd.DataFrame(lista_edit), use_container_width=True, num_rows="dynamic", hide_index=True)
        if st.button("💾 SALVA TUTTO"):
            final_rows = [[data_f, insegna_f, indirizzo_f, str(r['Prodotto']).upper(), clean_price(r['Prezzo Un.']) * float(r['Qtà']), 0, clean_price(r['Prezzo Un.']), r['Offerta'], r['Qtà'], "SI", str(r['Normalizzato']).upper()] for _, r in edited_df.iterrows()]
            worksheet.append_rows(final_rows)
            st.success("✅ Salvato!"); st.session_state.dati_analizzati = None; st.rerun()

# --- TAB 2: RICERCA (PUNTO 1 E 2 RICHIESTI) ---
with tab_cerca:
    # --- GESTIONE POSIZIONE (Punto 1) ---
    if st.session_state.my_lat:
        st.success(f"📍 Posizione impostata correttamente")
        if st.button("🔄 Modifica/Resetta Posizione"):
            st.session_state.my_lat = None
            st.session_state.my_lon = None
            st.rerun()
    else:
        with st.expander("📍 Imposta la tua posizione per calcolare le distanze", expanded=True):
            c_gps, c_man = st.columns([1, 2])
            with c_gps:
                if st.button("Usa GPS"):
                    loc = get_geolocation()
                    if loc:
                        st.session_state.my_lat = loc['coords']['latitude']
                        st.session_state.my_lon = loc['coords']['longitude']
                        st.rerun()
            with c_man:
                addr_in = st.text_input("Oppure scrivi indirizzo o città")
                if st.button("Imposta Indirizzo"):
                    lat, lon = get_coords_from_address(addr_in)
                    if lat:
                        st.session_state.my_lat, st.session_state.my_lon = lat, lon
                        st.rerun()
                    else: st.error("Indirizzo non trovato.")

    query = st.text_input("Cosa cerchi?", key="search_v30").upper().strip()
    
    if query:
        with st.spinner("Consultazione database..."):
            all_data = worksheet.get_all_records()
            if all_data:
                df_all = pd.DataFrame(all_data)
                df_all.columns = [str(c).strip() for c in df_all.columns]
                
                c_prod = get_col_name(df_all, 'PRODOTTO')
                c_norm = get_col_name(df_all, 'NORMALIZZAZIONE')
                c_super = get_col_name(df_all, 'SUPERMERCATO')
                c_indirizzo = get_col_name(df_all, 'INDIRIZZO')
                c_prezzo = get_col_name(df_all, 'NETTO') or get_col_name(df_all, 'UNITARIO')
                c_data = get_col_name(df_all, 'DATA')
                c_off = get_col_name(df_all, 'OFFERTA')

                mask = df_all[c_prod].astype(str).str.contains(query, na=False)
                if c_norm: mask |= df_all[c_norm].astype(str).str.contains(query, na=False)
                res = df_all[mask].copy()
                
                if not res.empty:
                    res[c_prezzo] = res[c_prezzo].apply(clean_price)
                    
                    def add_dist(row):
                        if not st.session_state.my_lat: return 999
                        addr_scontrino = re.sub(r'\W+', '', str(row[c_indirizzo])).upper()
                        neg = next((n for n in lista_negozi_raw if re.sub(r'\W+', '', str(n.get('Indirizzo_Standard (Pulito)', ''))).upper() == addr_scontrino), None)
                        if neg and neg.get('Latitudine'):
                            try:
                                return get_road_distance(st.session_state.my_lat, st.session_state.my_lon, float(str(neg['Latitudine']).replace(',','.')), float(str(neg['Longitudine']).replace(',','.')))
                            except: return 888
                        return 999

                    res['KM'] = res.apply(add_dist, axis=1)
                    res['dt'] = pd.to_datetime(res[c_data], format='%d/%m/%Y', errors='coerce')
                    res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=[c_super, c_indirizzo])
                    res = res.sort_values(by=c_prezzo)
                    
                    st.info(f"🏆 Il più economico: **{res.iloc[0][c_super]}** a **€{res.iloc[0][c_prezzo]:.2f}**")
                    
                    # --- RIORDINO COLONNE (Punto 2 richiesto) ---
                    # Ordine richiesto: Data, Normalizzato, Prezzo, Negozio, Indirizzo, Distanza
                    disp = res[[c_data, c_norm if c_norm else c_prod, c_prezzo, c_super, c_indirizzo, 'KM', c_off]]
                    disp.columns = ['Data', 'Articolo', 'Prezzo €', 'Negozio', 'Indirizzo', 'Km Strada', 'In Offerta']
                    
                    st.dataframe(disp, use_container_width=True, hide_index=True)
                else: st.warning("Nessun prodotto trovato.")
