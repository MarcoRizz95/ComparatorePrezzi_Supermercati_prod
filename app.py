import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re

# --- CONFIGURAZIONE PAGINA (Layout Moderno) ---
st.set_page_config(page_title="Scanner Spesa Pro", layout="centered", page_icon="🛒")

# CSS personalizzato per rendere l'interfaccia più "App"
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stButton>button { width: 100%; border-radius: 10px; height: 3em; background-color: #007bff; color: white; }
    .stDataFrame { border-radius: 10px; overflow: hidden; }
    .header-box { background-color: white; padding: 20px; border-radius: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); margin-bottom: 20px; }
    </style>
    """, unsafe_allow_key=True)

# --- FUNZIONI DI SERVIZIO ---
def clean_piva(piva):
    return re.sub(r'\D', '', str(piva)).zfill(11) # Forza a 11 cifre con zeri iniziali

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

# --- CONNESSIONE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    google_info = dict(st.secrets)
    creds = Credentials.from_service_account_info(google_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    lista_negozi = ws_negozi.get_all_records()
    model = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    st.error(f"Errore: {e}")
    st.stop()

if 'dati_analizzati' not in st.session_state:
    st.session_state.dati_analizzati = None

st.title("🛍️ Scanner Spesa")

# Sezione Caricamento
with st.container():
    uploaded_file = st.file_uploader("Scatta o carica lo scontrino", type=['jpg', 'jpeg', 'png'], label_visibility="collapsed")

if uploaded_file:
    img = ImageOps.exif_transpose(Image.open(uploaded_file))
    st.image(img, use_container_width=True)
    
    if st.button("🔍 Analizza Scontrino"):
        with st.spinner("L'IA sta leggendo i prodotti..."):
            prompt = """Analizza lo scontrino. Estrai: p_iva, indirizzo_letto, data_iso (YYYY-MM-DD). 
            Per ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard. 
            SCONTI: Sottrai righe negative al prodotto precedente. NO AGGREGAZIONE. Restituisci JSON."""
            response = model.generate_content([prompt, img])
            st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))

# --- REVISIONE DATI ---
if st.session_state.dati_analizzati:
    d = st.session_state.dati_analizzati
    testata = d.get('testata', {})
    
    # LOGICA DI MATCH P.IVA POTENZIATA (In Python, non IA)
    piva_letta = clean_piva(testata.get('p_iva', ''))
    match_negozio = next((n for n in lista_negozi if clean_piva(n['P_IVA']) == piva_letta), None)
    
    st.subheader("📝 Revisione Dati")
    
    with st.container():
        st.markdown('<div class="header-box">', unsafe_allow_key=True)
        c1, c2 = st.columns(2)
        with c1:
            insegna_def = match_negozio['Insegna_Standard'] if match_negozio else f"NUOVO ({piva_letta})"
            insegna_f = st.text_input("Supermercato", value=insegna_def).upper()
            data_iso = testata.get('data_iso', '2026-01-01')
            data_f = st.text_input("Data (DD/MM/YYYY)", value="/".join(data_iso.split("-")[::-1]))
        with c2:
            indirizzo_def = match_negozio['Indirizzo_Standard (Pulito)'] if match_negozio else testata.get('indirizzo_letto', '')
            indirizzo_f = st.text_input("Indirizzo", value=indirizzo_f_def if 'indirizzo_f_def' in locals() else indirizzo_def).upper()
        st.markdown('</div>', unsafe_allow_key=True)

    # Tabella Prodotti
    df = pd.DataFrame(d.get('prodotti', []))
    if not df.empty:
        df.columns = ['Prodotto', 'Prezzo', 'Qtà', 'Offerta', 'Normalizzato']
        df['Prezzo'] = df['Prezzo'].apply(lambda x: clean_price(x))
        
        st.write("### Elenco Articoli")
        edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic", hide_index=True)

        if st.button("💾 Salva nel Database"):
            try:
                final_rows = []
                for _, row in edited_df.iterrows():
                    p_unit = clean_price(row['Prezzo'])
                    final_rows.append([
                        data_f, insegna_f, indirizzo_f, str(row['Prodotto']).upper(),
                        p_unit * float(row['Qtà']), 0, p_unit, str(row['Offerta']).upper(),
                        row['Qtà'], "SI", str(row['Normalizzato']).upper()
                    ])
                worksheet.append_rows(final_rows)
                st.success(f"✅ Salvati {len(final_rows)} prodotti!")
                st.session_state.dati_analizzati = None
                st.rerun()
            except Exception as e:
                st.error(f"Errore: {e}")
