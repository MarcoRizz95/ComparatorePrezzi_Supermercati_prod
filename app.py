import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re
import requests # Serve per chiamare il servizio stradale
from streamlit_js_eval import streamlit_js_eval

# --- 1. FUNZIONI DI SERVIZIO ---

def get_road_distance(lat1, lon1, lat2, lon2):
    """Calcola la distanza STRADALE REALE tramite OSRM (Gratis)"""
    try:
        # OSRM vuole Longitudine,Latitudine
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        r = requests.get(url)
        data = r.json()
        if data['code'] == 'Ok':
            # La distanza è in metri, la convertiamo in km
            distanza_metri = data['routes'][0]['distance']
            return round(distanza_metri / 1000, 1)
    except:
        pass
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

# --- 2. CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Spesa Smart Strada", layout="centered", page_icon="🛒")

# --- 3. CONNESSIONE ---
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
    st.error(f"Errore: {e}")
    st.stop()

# --- 4. GPS UTENTE ---
user_pos = streamlit_js_eval(js_expressions="navigator.geolocation.getCurrentPosition(pos => pos.coords, err => console.log(err))", key="GPS")
my_lat, my_lon = None, None
if user_pos:
    my_lat, my_lon = user_pos.get('latitude'), user_pos.get('longitude')

# --- 5. LOGICA TABS ---
tab_carica, tab_cerca = st.tabs(["📷 CARICA SCONTRINO", "🔍 CERCA PREZZI"])

with tab_carica:
    if 'dati_analizzati' not in st.session_state:
        st.session_state.dati_analizzati = None
    files = st.file_uploader("Carica foto", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)
    if files:
        imgs = [ImageOps.exif_transpose(Image.open(f)) for f in files]
        if st.button("🚀 ANALIZZA"):
            with st.spinner("Analisi in corso..."):
                try:
                    negozi_str = ""
                    for i, n in enumerate(lista_negozi_raw):
                        negozi_str += f"ID {i}: {n['Insegna_Standard']} | {n['Indirizzo_Standard (Pulito)']} | P.IVA {n['P_IVA']}\n"
                    prompt = f""" 
                    ATTENZIONE: Se caricate più immagini, sono parti dello STESSO scontrino. Analizzale insieme come un unico documento.

                    Agisci come un contabile esperto / OCR. Analizza lo scontrino con queste REGOLE FISSE:

                    1. SCONTI: Se vedi 'SCONTO', 'FIDATY', prezzi negativi (es: 1,50-S o -0,90) o sconti con "%" 
                       NON creare nuove righe. Sottrai il valore al prodotto sopra.
                       Esempio: riga 1 'MOZZARELLA 4.00' e riga 2 'SCONTO -1.00' = Mozzarella a 3.00 (is_offerta: SI), senza estrarre la riga di sconto.

                    2. MOLTIPLICAZIONI: Se vedi '2 x 1.50' sopra un prodotto, 
                       prezzo_unitario è 1.50 e quantita è 2.

                    3. NORMALIZZAZIONE: Usa i nomi da questa lista se corrispondono: {glossario[:150]}

                    4. ESTREMA PRECISIONE: Non inventare prodotti e non "raggrupparli". Se ci sono due righe uguali, non salvarne una unica con prezzo e quantità doppie.
                       Ogni riga fisica deve essere letta.

                    JSON richiesto:
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
        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi_raw if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo_letto', ''))).upper()
        with c3: data_f = st.text_input("Data", value="/".join(testata.get('data_iso', '2026-01-01').split("-")[::-1]))
        lista_edit = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Normalizzato": str(p.get('nome_standard', p.get('nome_letto', ''))).upper()} for p in d.get('prodotti', [])]
        edited_df = st.data_editor(pd.DataFrame(lista_edit), use_container_width=True, num_rows="dynamic", hide_index=True)
        if st.button("💾 SALVA"):
            final_rows = [[data_f, insegna_f, indirizzo_f, str(r['Prodotto']).upper(), clean_price(r['Prezzo Un.']) * float(r['Qtà']), 0, clean_price(r['Prezzo Un.']), r['Offerta'], r['Qtà'], "SI", str(r['Nome Standard']).upper()] for _, r in edited_df.iterrows()]
            worksheet.append_rows(final_rows)
            st.success("Salvataggio completato!"); st.session_state.dati_analizzati = None; st.rerun()

with tab_cerca:
    st.write("## 🔍 Cerca Prezzi e Distanza Stradale")
    if not user_pos: st.warning("📍 Attiva il GPS per calcolare i km di strada.")
    query = st.text_input("Cosa cerchi?", key="search_q").upper().strip()
    if query:
        with st.spinner("Consultazione database e calcolo percorsi..."):
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

                mask = df_all[c_prod].astype(str).str.contains(query, na=False)
                if c_norm: mask |= df_all[c_norm].astype(str).str.contains(query, na=False)
                res = df_all[mask].copy()
                
                if not res.empty:
                    res[c_prezzo] = res[c_prezzo].apply(clean_price)
                    
                    # --- CALCOLO DISTANZA STRADALE ---
                    def add_road_dist(row):
                        neg = next((n for n in lista_negozi_raw if n['Indirizzo_Standard (Pulito)'].upper() == row[c_indirizzo].upper()), None)
                        if neg and neg.get('Latitudine') and my_lat:
                            # Chiamata al servizio stradale gratuito OSRM
                            return get_road_distance(my_lat, my_lon, neg['Latitudine'], neg['Longitudine'])
                        return 999 

                    res['KM_Strada'] = res.apply(add_road_dist, axis=1)
                    res['dt'] = pd.to_datetime(res[c_data], format='%d/%m/%Y', errors='coerce')
                    res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=[c_super, c_indirizzo])
                    res = res.sort_values(by=c_prezzo)
                    
                    best = res.iloc[0]
                    st.info(f"🏆 Più economico: **{best[c_super]}** a **€{best[c_prezzo]:.2f}** ({best['KM_Strada']} km di strada)")
                    
                    disp = res[[c_prezzo, 'KM_Strada', c_super, c_indirizzo, c_data]]
                    disp.columns = ['€ Prezzo', 'Km Strada', 'Negozio', 'Indirizzo', 'Data']
                    st.dataframe(disp.sort_values(by='€ Prezzo'), use_container_width=True, hide_index=True)
                else: st.warning("Nessun prodotto trovato.")
