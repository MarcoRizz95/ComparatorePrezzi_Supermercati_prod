import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image
from PIL import Image, ImageOps # Aggiunto ImageOps per la rotazione
import pandas as pd # Aggiunto per gestire le tabelle editabili

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Master Scanner V11", layout="centered", page_icon="🛒")
st.set_page_config(page_title="Scanner Prezzi V12", layout="wide", page_icon="🛒")

# --- CONNESSIONE AI E DATABASE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)

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
    
    google_info = dict(st.secrets) # Carica tutti i segreti piatti
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    
    # Foglio Database Prezzi (Primo tab)
    worksheet = sh.get_worksheet(0)
    
    # Lettura Anagrafe Negozi (Secondo tab - 4 COLONNE)
    try:
        ws_negozi = sh.worksheet("Anagrafe_Negozi")
        lista_negozi = ws_negozi.get_all_records()
    except:
        lista_negozi = []
        st.error("Errore: Tab 'Anagrafe_Negozi' non trovato o colonne non corrette.")
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    lista_negozi = ws_negozi.get_all_records()

    # Lettura Glossario Prodotti per Normalizzazione
    try:
        dati_db = worksheet.get_all_records()
        glossario_prodotti = list(set([str(r.get('Nome Standard Proposto', '')).upper() for r in dati_db if r.get('Nome Standard Proposto')]))
    except:
        glossario_prodotti = []

    # Modello Gemini 1.5 Pro
    model = genai.GenerativeModel('models/gemini-2.5-flash')
    
    model = genai.GenerativeModel('models/gemini-1.5-pro')
except Exception as e:
    st.error(f"Errore di configurazione: {e}")
    st.error(f"Errore configurazione: {e}")
    st.stop()

# --- INIZIALIZZAZIONE MEMORIA DI SESSIONE ---
if 'dati_analizzati' not in st.session_state:
    st.session_state.dati_analizzati = None

# --- INTERFACCIA ---
st.title("🛒 Scanner Scontrini - v3")
st.write("Versione 11 - Match Punti Vendita Multilivello")
st.title("🛒 Scanner Scontrini Editabile")

uploaded_file = st.file_uploader("Carica o scatta una foto dello scontrino", type=['jpg', 'jpeg', 'png'])
uploaded_file = st.file_uploader("Scatta o carica foto", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="Scontrino caricato", use_container_width=True)
    # 1. FIX ROTAZIONE IMMAGINE
    img_raw = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img_raw) # Raddrizza la foto basandosi sui sensori del telefono
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.image(img, caption="Scontrino acquisito", use_container_width=True)

    if st.button("Analizza e Salva"):
        with st.spinner("Analisi e ricerca match in corso..."):
            try:
                # Costruiamo l'elenco dei negozi per l'IA (usando le tue 4 colonne)
    with col2:
        if st.button("🚀 Inizia Analisi AI"):
            with st.spinner("L'IA sta leggendo..."):
                negozi_str = ""
                for i, n in enumerate(lista_negozi):
                    negozi_str += f"ID {i}: {n['Insegna_Standard']} | P.IVA: {n['P_IVA']} | Indirizzo Scontrino: {n['Indirizzo_Scontrino (Grezzo)']} | Indirizzo Pulito: {n['Indirizzo_Standard (Pulito)']}\n"
                    negozi_str += f"ID {i}: {n['Insegna_Standard']} | {n['Indirizzo_Scontrino (Grezzo)']} | P.IVA {n['P_IVA']}\n"

                prompt = f"""
                Analizza questo scontrino.
                
                NEGOZI CONOSCIUTI:
                {negozi_str}

                PRODOTTI CONOSCIUTI:
                {", ".join(glossario_prodotti[:100])}

                ISTRUZIONI:
                1. Estrai DATA (YYYY-MM-DD), P_IVA e INDIRIZZO dallo scontrino.
                2. Trova il match con 'NEGOZI CONOSCIUTI'. Restituisci l'ID se corrisponde P.IVA e l'indirizzo è simile a uno degli indirizzi conosciuti. Altrimenti 'NUOVO'.
                3. Estrai ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard (se simile a prodotti conosciuti).
                4. SCONTI: Sottrai righe negative al prodotto precedente.
                5. NO AGGREGAZIONE: Una riga per ogni articolo fisico.

                RISPONDI SOLO JSON:
                {{
                  "match_id": "ID o NUOVO",
                  "testata": {{ "p_iva": "", "indirizzo_letto": "", "data_iso": "" }},
                  "prodotti": [
                    {{ "nome_letto": "", "prezzo_unitario": 0.0, "quantita": 1, "is_offerta": "SI/NO", "nome_standard": "" }}
                  ]
                }}
                """
                prompt = f"""Analizza lo scontrino. Negozi conosciuti: {negozi_str}. 
                Restituisci JSON con: match_id (ID o NUOVO), testata (p_iva, indirizzo_letto, data_iso), 
                prodotti (nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard)."""

                response = model.generate_content([prompt, img])
                dati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                
                st.subheader("Payload JSON")
                st.json(dati)

                # --- ELABORAZIONE E NORMALIZZAZIONE ---
                testata = dati.get('testata', {})
                match_id = dati.get('match_id', 'NUOVO')
                
                if str(match_id).isdigit() and int(match_id) < len(lista_negozi):
                    # Match trovato: usiamo i dati dell'anagrafe
                    negozio = lista_negozi[int(match_id)]
                    insegna = str(negozio['Insegna_Standard']).upper()
                    indirizzo = str(negozio['Indirizzo_Standard (Pulito)']).upper()
                else:
                    # Nessun match: usiamo dati grezzi
                    p_iva = str(testata.get('p_iva', '')).replace(' ', '')
                    insegna = f"NUOVO ({p_iva})".upper()
                    indirizzo = str(testata.get('indirizzo_letto', 'DA VERIFICARE')).upper()

                # Formattazione Data (YYYY-MM-DD -> DD/MM/YYYY)
                d_raw = testata.get('data_iso', '2026-01-01')
                try:
                    y, m, d = d_raw.split('-')
                    data_ita = f"{d}/{m}/{y}"
                except:
                    data_ita = d_raw

                # Creazione righe
                righe_da_scrivere = []
                for p in dati.get('prodotti', []):
                    p_unitario = float(p.get('prezzo_unitario', 0))
                    qt = float(p.get('quantita', 1))
                    
                    righe_da_scrivere.append([
                        data_ita,
                        insegna,
                        indirizzo,
                        str(p.get('nome_letto', '')).upper(),
                        p_unitario * qt,
                        0, # Sconto (già calcolato)
                        p_unitario,
                        p.get('is_offerta', 'NO'),
                        qt,
                        "SI",
                        str(p.get('nome_standard', p.get('nome_letto', ''))).upper()
                    ])
                res_json = json.loads(response.text.strip().replace('```json', '').replace('```', ''))

                if righe_da_scrivere:
                    worksheet.append_rows(righe_da_scrivere)
                    st.success(f"✅ Salvati {len(righe_da_scrivere)} prodotti per {insegna}!")
                else:
                    st.warning("Nessun prodotto trovato.")
                # Salviamo i dati nella sessione per permettere la modifica
                st.session_state.dati_analizzati = res_json
                st.success("Analisi completata! Ora puoi controllare e modificare i dati qui sotto.")

# --- STEP DI CONTROLLO E MODIFICA ---
if st.session_state.dati_analizzati:
    st.divider()
    st.subheader("📝 Revisione Dati Estratti")
    
    dati = st.session_state.dati_analizzati
    testata = dati.get('testata', {})
    match_id = dati.get('match_id', 'NUOVO')

    # Parte 1: Testata (Insegna e Data)
    c1, c2, c3 = st.columns(3)
    with c1:
        # Se c'è un match_id, pre-impostiamo il nome dall'anagrafe
        nome_default = testata.get('insegna_letta', 'SCONOSCIUTO')
        if str(match_id).isdigit():
            nome_default = lista_negozi[int(match_id)]['Insegna_Standard']
        insegna_final = st.text_input("Insegna Supermercato", value=nome_default).upper()
    
    with c2:
        indirizzo_default = testata.get('indirizzo_letto', 'DA VERIFICARE')
        if str(match_id).isdigit():
            indirizzo_default = lista_negozi[int(match_id)]['Indirizzo_Standard (Pulito)']
        indirizzo_final = st.text_input("Indirizzo Punto Vendita", value=indirizzo_default).upper()
    
    with c3:
        data_final = st.text_input("Data (DD/MM/YYYY)", value="/".join(testata.get('data_iso', '2026-01-01').split('-')[::-1]))

            except Exception as e:
                st.error(f"Errore: {e}")
    # Parte 2: Tabella Prodotti Editabile
    df_prodotti = pd.DataFrame(dati.get('prodotti', []))
    
    # Rinominiamo le colonne per chiarezza nell'editor
    df_prodotti = df_prodotti.rename(columns={
        'nome_letto': 'Prodotto (Scontrino)',
        'prezzo_unitario': 'Prezzo Unitario',
        'quantita': 'Qtà',
        'is_offerta': 'In Offerta',
        'nome_standard': 'Nome Normalizzato'
    })

    st.write("Modifica i prodotti direttamente nella tabella:")
    edited_df = st.data_editor(df_prodotti, use_container_width=True, num_rows="dynamic")

    # BOTTONE FINALE DI CONFERMA
    if st.button("💾 Conferma e Salva nel Database"):
        try:
            nuove_righe = []
            for _, row in edited_df.iterrows():
                nuove_righe.append([
                    data_final,
                    insegna_final,
                    indirizzo_final,
                    str(row['Prodotto (Scontrino)']).upper(),
                    float(row['Prezzo Unitario']) * float(row['Qtà']),
                    0, # Sconto
                    float(row['Prezzo Unitario']),
                    row['In Offerta'],
                    row['Qtà'],
                    "SI",
                    str(row['Nome Normalizzato']).upper()
                ])
            
            worksheet.append_rows(nuove_righe)
            st.balloons()
            st.success("✅ Dati salvati con successo! Database aggiornato.")
            # Resettiamo la sessione per il prossimo scontrino
            st.session_state.dati_analizzati = None
            st.button("Carica un altro scontrino")
        except Exception as e:
