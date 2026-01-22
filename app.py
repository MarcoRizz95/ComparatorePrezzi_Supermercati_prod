import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Scanner Spesa Pro", layout="centered", page_icon="🛒")

# CSS per il look "App" (Corretto unsafe_allow_html)
st.markdown("""
    <style>
    .stApp { background-color: #f8f9fa; }
    .header-box { 
        background-color: #ffffff; 
        padding: 25px; 
        border-radius: 15px; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.1); 
        margin-bottom: 20px;
        border: 1px solid #e9ecef;
    }
    .stButton>button { width: 100%; border-radius: 12px; height: 3.5em; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNZIONI DI SERVIZIO ---
def clean_piva(piva):
    # Estrae solo i numeri e forza a 11 cifre aggiungendo zeri se mancano
    solo_numeri = re.sub(r'\D', '', str(piva))
    if not solo_numeri: return ""
    return solo_numeri.zfill(11)

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

# --- CONNESSIONE AI E DATABASE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    
    # Carichiamo credenziali Google Sheets
    google_info = dict(st.secrets)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    lista_negozi = ws_negozi.get_all_records()
    
    # IMPOSTATO MODELLO GEMINI 2.5 FLASH
    model = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    st.error(f"Errore inizializzazione: {e}")
    st.stop()

if 'dati_analizzati' not in st.session_state:
    st.session_state.dati_analizzati = None

# --- INTERFACCIA ---
st.title("🛍️ Scanner Spesa")

uploaded_file = st.file_uploader("Carica scontrino", type=['jpg', 'jpeg', 'png'], label_visibility="collapsed")

if uploaded_file:
    img = ImageOps.exif_transpose(Image.open(uploaded_file))
    st.image(img, use_container_width=True)
    
    if st.button("🔍 ANALIZZA SCONTRINO"):
        with st.spinner("Analisi con Gemini 2.5 Flash in corso..."):
            prompt = """Analizza lo scontrino. Estrai: p_iva, indirizzo_letto, data_iso (YYYY-MM-DD). 
            Per ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard. 
            SCONTI: Sottrai righe negative al prodotto precedente. NO AGGREGAZIONE. Restituisci JSON."""
            response = model.generate_content([prompt, img])
            st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))

# --- REVISIONE DATI ---
if st.session_state.dati_analizzati:
    d = st.session_state.dati_analizzati
    testata = d.get('testata', {})
    
    # Match P.IVA (Eseguito in Python)
    piva_letta = clean_piva(testata.get('p_iva', ''))
    match_negozio = next((n for n in lista_negozi if clean_piva(n['P_IVA']) == piva_letta), None)
    
    st.subheader("📝 Revisione Dati")
    
    # Box Bianco per la Testata
    with st.container():
        # Definiamo i valori di default basandoci sull'anagrafe
        if match_negozio:
            insegna_def = match_negozio['Insegna_Standard']
            indirizzo_def = match_negozio['Indirizzo_Standard (Pulito)']
        else:
            insegna_def = f"NUOVO ({piva_letta})"
            indirizzo_def = testata.get('indirizzo_letto', '')

        data_iso = testata.get('data_iso', '2026-01-01')
        data_f_def = "/".join(data_iso.split("-")[::-1])

        st.markdown('<div class="header-box">', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            insegna_f = st.text_input("Supermercato", value=insegna_def).upper()
            data_f = st.text_input("Data (DD/MM/YYYY)", value=data_f_def)
        with c2:
            indirizzo_f = st.text_input("Indirizzo", value=indirizzo_def).upper()
        st.markdown('</div>', unsafe_allow_html=True)

    # Tabella Prodotti
    df = pd.DataFrame(d.get('prodotti', []))
    if not df.empty:
        # Pulizia estetica DataFrame
        df.columns = ['Prodotto', 'Prezzo Un.', 'Qtà', 'Offerta', 'Nome Standard']
        df['Prezzo Un.'] = df['Prezzo Un.'].apply(clean_price)
        
        st.write("### Articoli rilevati")
        edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic", hide_index=True)

        if st.button("💾 SALVA NEL DATABASE"):
            try:
                final_rows = []
                for _, row in edited_df.iterrows():
                    p_unit = clean_price(row['Prezzo Un.'])
                    qta = float(row['Qtà'])
                    final_rows.append([
                        data_f, insegna_f, indirizzo_f, str(row['Prodotto']).upper(),
                        p_unit * qta, 0, p_unit, str(row['Offerta']).upper(),
                        qta, "SI", str(row['Nome Standard']).upper()
                    ])
                worksheet.append_rows(final_rows)
                st.balloons()
                st.success(f"✅ Salvati {len(final_rows)} prodotti!")
