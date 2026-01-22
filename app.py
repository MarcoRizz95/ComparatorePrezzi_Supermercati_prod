import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Scanner Spesa", layout="centered", page_icon="🛒")

st.markdown("""
    <style>
    .header-box { padding: 20px; border-radius: 15px; border: 2px solid #e0e0e0; margin-bottom: 20px; }
    .stButton>button { width: 100%; border-radius: 10px; height: 3.5em; font-weight: bold; background-color: #007bff; color: white; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNZIONI DI SERVIZIO ---
def clean_piva(piva):
    if not piva: return ""
    solo_numeri = re.sub(r'\D', '', str(piva))
    return solo_numeri.zfill(11) if solo_numeri else ""

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
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    lista_negozi = ws_negozi.get_all_records()
    model = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    st.error(f"Errore inizializzazione: {e}")
    st.stop()

if 'dati_analizzati' not in st.session_state:
    st.session_state.dati_analizzati = None

st.title("🛍️ Scanner Spesa")

uploaded_file = st.file_uploader("Carica o scatta una foto dello scontrino", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    img = ImageOps.exif_transpose(Image.open(uploaded_file))
    st.image(img, use_container_width=True)
    
    if st.button("🔍 ANALIZZA ORA"):
        with st.spinner("L'IA sta leggendo..."):
            try:
                prompt = """Analizza lo scontrino. Estrai: p_iva, indirizzo_letto, data_iso (YYYY-MM-DD). 
                Per ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard. 
                SCONTI: Sottrai righe negative al prodotto precedente. NO AGGREGAZIONE. Restituisci JSON."""
                response = model.generate_content([prompt, img])
                st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                st.rerun()
            except Exception as e:
                st.error(f"Errore analisi: {e}")

if st.session_state.dati_analizzati:
    st.divider()
    st.subheader("📝 Revisione Dati")
    
    d = st.session_state.dati_analizzati
    testata = d.get('testata', {})
    
    # Match P.IVA sicuro
    piva_letta = clean_piva(testata.get('p_iva', ''))
    match_negozio = next((n for n in lista_negozi if clean_piva(n.get('P_IVA', '')) == piva_letta), None) if piva_letta else None
    
    # Determinazione valori suggeriti
    if match_negozio:
        insegna_def = match_negozio['Insegna_Standard']
        indirizzo_def = match_negozio['Indirizzo_Standard (Pulito)']
    else:
        insegna_def = f"NUOVO ({piva_letta})" if piva_letta else "SCONOSCIUTO"
        indirizzo_def = testata.get('indirizzo_letto', '')

    # Formattazione data
    data_iso = testata.get('data_iso', '2026-01-01')
    try:
        y, m, day = data_iso.split("-")
        data_f_def = f"{day}/{m}/{y}"
    except:
        data_f_def = data_iso

    st.markdown('<div class="header-box">', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        insegna_f = st.text_input("Supermercato", value=insegna_def).upper()
        data_f = st.text_input("Data scontrino", value=data_f_def)
    with c2:
        indirizzo_f = st.text_input("Indirizzo punto vendita", value=indirizzo_def).upper()
    st.markdown('</div>', unsafe_allow_html=True)

    # --- CREAZIONE TABELLA ROBUSTA ---
    prodotti_raw = d.get('prodotti', [])
    # Costruiamo noi la lista di dizionari con chiavi fisse per evitare il ValueError
    lista_pulita = []
    for p in prodotti_raw:
        lista_pulita.append({
            "Prodotto": str(p.get('nome_letto', '')).upper(),
            "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)),
            "Qtà": float(p.get('quantita', 1)),
            "Offerta": str(p.get('is_offerta', 'NO')).upper(),
            "Nome Standard": str(p.get('nome_standard', '')).upper()
        })
    
    if lista_pulita:
        df = pd.DataFrame(lista_pulita)
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
                st.success("✅ Salvataggio completato!")
                st.session_state.dati_analizzati = None
                st.rerun()
            except Exception as e:
                st.error(f"Errore durante il salvataggio: {e}")
