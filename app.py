import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Scanner Prezzi V12", layout="wide", page_icon="🛒")

# --- CONNESSIONE AI E DATABASE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    
    # Caricamento credenziali Google (struttura piatta)
    google_info = {
        "type": st.secrets["type"],
        "project_id": st.secrets["project_id"],
        "private_key_id": st.secrets["private_key_id"],
        "private_key": st.secrets["private_key"],
        "client_email": st.secrets["client_email"],
        "client_id": st.secrets["client_id"],
        "auth_uri": st.secrets["auth_uri"],
        "token_uri": st.secrets["token_uri"],
        "auth_provider_x509_cert_url": st.secrets["auth_provider_x509_cert_url"],
        "client_x509_cert_url": st.secrets["client_x509_cert_url"]
    }
    
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    lista_negozi = ws_negozi.get_all_records()

    # Modello Pro per massima precisione
    model = genai.GenerativeModel('models/gemini-1.5-pro')
except Exception as e:
    st.error(f"Errore configurazione iniziale: {e}")
    st.stop()

# --- MEMORIA DI SESSIONE ---
if 'dati_analizzati' not in st.session_state:
    st.session_state.dati_analizzati = None

# --- INTERFACCIA ---
st.title("🛒 Scanner Scontrini con Revisione")

uploaded_file = st.file_uploader("Scatta o carica foto", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    # Fix rotazione immagine
    img_raw = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img_raw)
    
    col_img, col_btn = st.columns([1, 1])
    
    with col_img:
        st.image(img, caption="Scontrino acquisito", use_container_width=True)

    with col_btn:
        st.write("### Azioni")
        if st.button("🚀 Avvia Analisi AI"):
            with st.spinner("L'IA sta leggendo lo scontrino..."):
                try:
                    # Preparazione contesto negozi
                    negozi_str = ""
                    for i, n in enumerate(lista_negozi):
                        negozi_str += f"ID {i}: {n['Insegna_Standard']} | {n['Indirizzo_Scontrino (Grezzo)']} | P.IVA {n['P_IVA']}\n"

                    prompt = f"""Analizza lo scontrino. 
                    NEGOZI CONOSCIUTI: {negozi_str}
                    REGOLE: 
                    - Sconti: sottrai al prodotto sopra.
                    - Moltiplicazioni: usa il prezzo unitario.
                    - Match Negozio: restituisci l'ID se lo trovi, altrimenti 'NUOVO'.
                    RESTITUISCI SOLO JSON:
                    {{
                      "match_id": "ID o NUOVO",
                      "testata": {{ "p_iva": "", "indirizzo_letto": "", "data_iso": "YYYY-MM-DD" }},
                      "prodotti": [
                        {{ "nome_letto": "", "prezzo_unitario": 0.0, "quantita": 1, "is_offerta": "SI/NO", "nome_standard": "" }}
                      ]
                    }}"""
                    
                    response = model.generate_content([prompt, img])
                    res_json = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                    
                    # Salvataggio in sessione
                    st.session_state.dati_analizzati = res_json
                    st.success("Analisi completata! Controlla i dati qui sotto.")
                except Exception as e:
                    st.error(f"Errore analisi: {e}")

# --- AREA DI REVISIONE (appare solo dopo l'analisi) ---
if st.session_state.dati_analizzati:
    st.divider()
    st.subheader("📝 Controlla e Modifica i Dati")
    
    dati = st.session_state.dati_analizzati
    testata = dati.get('testata', {})
    match_id = dati.get('match_id', 'NUOVO')

    # Sezione 1: Testata editabile
    c1, c2, c3 = st.columns(3)
    with c1:
        # Recupero nome da anagrafe se c'è un match
        n_def = testata.get('p_iva', 'SCONOSCIUTO')
        if str(match_id).isdigit() and int(match_id) < len(lista_negozi):
            n_def = lista_negozi[int(match_id)]['Insegna_Standard']
        insegna_f = st.text_input("Supermercato", value=str(n_def).upper())
    
    with c2:
        i_def = testata.get('indirizzo_letto', 'DA VERIFICARE')
        if str(match_id).isdigit() and int(match_id) < len(lista_negozi):
            i_def = lista_negozi[int(match_id)]['Indirizzo_Standard (Pulito)']
        indirizzo_f = st.text_input("Indirizzo", value=str(i_def).upper())
    
    with c3:
        # Formattazione data YYYY-MM-DD -> DD/MM/YYYY
        d_iso = testata.get('data_iso', '2026-01-01')
        try:
            d_ita = "/".join(d_iso.split("-")[::-1])
        except:
            d_ita = d_iso
        data_f = st.text_input("Data scontrino", value=d_ita)

    # Sezione 2: Tabella Prodotti Editabile
    df = pd.DataFrame(dati.get('prodotti', []))
    if not df.empty:
        # Rinominazione colonne per l'utente
        df.columns = ['Prodotto (Scontrino)', 'Prezzo Unit.', 'Qtà', 'Offerta', 'Nome Standard']
        st.write("Puoi modificare i prezzi e i nomi cliccando nelle celle:")
        edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")

        # Bottone di salvataggio finale
        if st.button("💾 Conferma e Salva tutto nel Database"):
            try:
                final_rows = []
                for _, row in edited_df.iterrows():
                    final_rows.append([
                        data_f, insegna_f, indirizzo_f,
                        str(row['Prodotto (Scontrino)']).upper(),
                        float(row['Prezzo Unit.']) * float(row['Qtà']),
                        0, # Sconto (già calcolato)
                        float(row['Prezzo Unit.']),
                        row['Offerta'],
                        row['Qtà'],
                        "SI",
                        str(row['Nome Standard']).upper()
                    ])
                
                worksheet.append_rows(final_rows)
                st.balloons()
                st.success(f"✅ Grandioso! {len(final_rows)} righe salvate correttamente.")
                st.session_state.dati_analizzati = None # Reset
            except Exception as e:
                st.error(f"Errore salvataggio: {e}")
