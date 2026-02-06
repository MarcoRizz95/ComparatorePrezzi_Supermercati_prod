import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re
import requests
import uuid
import math 
import time
import itertools
from streamlit_js_eval import get_geolocation
from geopy.geocoders import Nominatim

# --- 1. FUNZIONI DI SERVIZIO ---

def get_road_distance(lat1, lon1, lat2, lon2):
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        r = requests.get(url, timeout=3)
        data = r.json()
        if data['code'] == 'Ok':
            return round(data['routes'][0]['distance'] / 1000, 1)
    except: pass
    return None

def get_coords_from_address(address):
    try:
        geolocator = Nominatim(user_agent="comparatore_spesa_v32_final")
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

def generate_short_id():
    return str(uuid.uuid4())[:8]

def sanitize_value(val):
    """Pulisce i valori per evitare errori JSON in Google Sheets"""
    if val is None: return ""
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val): return 0.0
    return val

# --- 2. CONNESSIONE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    google_info = dict(st.secrets)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    
    sh = gc.open("Database_Prezzi")
    ws_scontrini = sh.worksheet("Scontrini") 
    ws_catalogo = sh.worksheet("Catalogo")
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    
    lista_negozi_raw = ws_negozi.get_all_records()
    model = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    st.error(f"Errore connessione: {e}")
    st.stop()

# --- 3. GESTIONE POSIZIONE E STATO ---
if 'my_lat' not in st.session_state: st.session_state.my_lat = None
if 'my_lon' not in st.session_state: st.session_state.my_lon = None
# Chiave per resettare l'uploader dopo il salvataggio
if 'uploader_key' not in st.session_state: st.session_state.uploader_key = 0

st.title("🛍️ Spesa Normalizzata & Geolocalizzata - VERSIONE PROD.")

tab_carica, tab_cerca, tab_carrello = st.tabs(["📷 CARICA", "🔍 CERCA PRODOTTO", "🛒 CARRELLO OTTIMIZZATO"])

# --- TAB 1: CARICAMENTO ---
with tab_carica:
    if 'dati_analizzati' not in st.session_state: st.session_state.dati_analizzati = None
    
    files = st.file_uploader(
        "Carica scontrini", 
        type=['jpg', 'jpeg', 'png'], 
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}"
    )
    
    if files:
        imgs = [ImageOps.exif_transpose(Image.open(f)) for f in files]
        st.image(imgs, width=150)
        
        if st.button("🚀 ANALIZZA E NORMALIZZA"):
            with st.spinner("Analisi scontrino in corso..."):
                try:
                    # Carichiamo nomi noti per aiutare il matching
                    try:
                        catalogo_raw = ws_catalogo.get_all_records()
                        nomi_noti = list(set([r['NOME_NORMALIZZATO'] for r in catalogo_raw if r['NOME_NORMALIZZATO']]))
                    except: nomi_noti = []
                    
                    # --- PROMPT IBRIDO (CONTABILE + DATA MANAGER + SCONTRINO ID) ---
                    prompt = f"""
                    Agisci con due ruoli simultanei: 
                    1. CONTABILE (per i calcoli di cassa precisi)
                    2. DATA MANAGER (per la normalizzazione del database)

                    Analizza le immagini dello scontrino seguendo rigorosamente queste FASI:

                    --- FASE 1: TESTATA E IDENTIFICATIVI ---
                    Cerca:
                    - P.IVA (solo cifre)
                    - Indirizzo completo
                    - Data (YYYY-MM-DD)
                    - NUMERO SCONTRINO: Cerca etichette come 'Scontrino n.', 'Doc.', 'RT', 'SF', '#'. Estrai il codice identificativo univoco.

                    --- FASE 2: PULIZIA CONTABILE (Regole 'V18') ---
                    A. SCONTI E PREZZI NEGATIVI: 
                       Se vedi righe come 'SCONTO', 'FIDATY', o importi col segno meno (-0.50) subito sotto un prodotto:
                       - NON creare una riga per lo sconto.
                       - SOTTRAI il valore al prezzo del prodotto sopra. 
                       - Imposta 'is_offerta' su "SI".

                    B. MOLTIPLICATORI:
                       Se vedi '3 x 1.50' (3 pezzi a 1.50 l'uno):
                       - 'quantita_acquistata' = 3
                       - 'prezzo_unitario' = 1.50

                    --- FASE 3: ESTRAZIONE E NORMALIZZAZIONE DATABASE ---
                    Per ogni riga risultante dalla Fase 2, estrai:
                    
                    1. 'nome_grezzo': Testo originale.
                    2. 'nome_normalizzato': Nome standard descrittivo (es. 'LATTE GRANAROLO P.S. 1L').
                       - Se simile a questi, usa ESATTAMENTE questo nome: {nomi_noti[:50]}
                    3. 'brand': Marca (es. GRANAROLO). Se non c'è, 'GENERICO'.
                    4. 'categoria': Macro categoria (es. LATTE, PASTA).
                    5. 'formato': SOLO IL NUMERO (es. 1.0, 0.5).
                    6. 'unita': SOLO 'KG', 'L', 'PZ'. Converti tutto (500ml -> 0.5 L).

                    OUTPUT JSON:
                    {{
                      "testata": {{ "p_iva": "", "indirizzo": "", "data_iso": "", "num_scontrino": "" }},
                      "prodotti": [
                        {{
                          "nome_grezzo": "...", "nome_normalizzato": "...", "brand": "...", "categoria": "...",
                          "formato": 1.0, "unita": "L", "prezzo_unitario": 0.0, "quantita_acquistata": 1, "is_offerta": "NO"
                        }}
                      ]
                    }}
                    """
                    response = model.generate_content([prompt, *imgs])
                    text_resp = response.text.strip().replace('```json', '').replace('```', '')
                    st.session_state.dati_analizzati = json.loads(text_resp)
                    st.rerun()
                except Exception as e: st.error(f"Errore IA: {e}")

    # --- UI DI REVISIONE ---
    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        prodotti = d.get('prodotti', [])
        
        # Calcolo Totale
        tot_calc = sum([clean_price(p.get('prezzo_unitario', 0)) * float(p.get('quantita_acquistata', 1)) for p in prodotti])

        # Match Negozio
        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi_raw if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        
        st.markdown("### 🧾 Dettagli Scontrino")
        c1, c2, c3, c4 = st.columns(4)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: data_f = st.text_input("Data", value=testata.get('data_iso', '2026-01-01'))
        with c3: num_scontrino_f = st.text_input("N. Scontrino", value=testata.get('num_scontrino', '')).upper()
        with c4: st.metric("Totale Letto", f"€ {tot_calc:.2f}")
        
        indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo', ''))).upper()

        st.markdown("### 🛒 Prodotti (Normalizzazione)")
        
        # Editor Tabella
        df_editor = pd.DataFrame(prodotti)
        col_map = {
            "nome_grezzo": "Scontrino", "nome_normalizzato": "Nome Catalogo (Editabile)", 
            "prezzo_unitario": "Prezzo €", "quantita_acquistata": "Qtà",
            "formato": "Peso/Vol (Tot)", "unita": "Unità (KG/L/PZ)",
            "brand": "Marca", "categoria": "Cat", "is_offerta": "Offerta"
        }
        # Aggiunta colonne mancanti per sicurezza
        for k in col_map.keys():
            if k not in df_editor.columns: df_editor[k] = ""
            
        df_editor = df_editor.rename(columns=col_map)
        edited_df = st.data_editor(df_editor, use_container_width=True, num_rows="dynamic", hide_index=True)

        if st.button("💾 SALVA NEL DATABASE RELAZIONALE"):
            with st.spinner("Salvataggio e pulizia in corso..."):
                
                # 1. Controlli Catalogo
                try:
                    if not ws_catalogo.get_all_values():
                        ws_catalogo.append_row(["ID_PRODOTTO", "NOME_NORMALIZZATO", "BRAND", "CATEGORIA", "FORMATO", "UNITA"])
                except: pass
                
                try:
                    cat_records = ws_catalogo.get_all_records()
                    df_cat = pd.DataFrame(cat_records)
                except: df_cat = pd.DataFrame()
                
                rows_scontrini = []
                rows_catalogo_new = []
                
                for idx, row in edited_df.iterrows():
                    # Preparazione Dati Puliti
                    norm_name = str(row["Nome Catalogo (Editabile)"]).upper().strip()
                    brand = str(row["Marca"]).upper().strip()
                    cat = str(row["Cat"]).upper().strip()
                    unit = str(row["Unità (KG/L/PZ)"]).upper().strip()
                    
                    try: fmt = float(str(row["Peso/Vol (Tot)"]).replace(',', '.'))
                    except: fmt = 1.0
                    fmt = sanitize_value(fmt)
                    
                    # LOGICA ID (Relazionale)
                    prod_id = None
                    # A. Cerca nel DB
                    if not df_cat.empty and 'NOME_NORMALIZZATO' in df_cat.columns:
                        match_prod = df_cat[df_cat['NOME_NORMALIZZATO'] == norm_name]
                        if not match_prod.empty: prod_id = str(match_prod.iloc[0]['ID_PRODOTTO'])
                    
                    # B. Cerca nei Nuovi
                    if not prod_id:
                        for new_p in rows_catalogo_new:
                            if new_p[1] == norm_name:
                                prod_id = str(new_p[0]); break
                    
                    # C. Crea Nuovo
                    if not prod_id:
                        prod_id = generate_short_id()
                        rows_catalogo_new.append([str(prod_id), norm_name, brand, cat, fmt, unit])
                    
                    # Prezzi e Totali
                    try: p_unit = float(str(row["Prezzo €"]).replace(',', '.'))
                    except: p_unit = 0.0
                    try: qta = float(str(row["Qtà"]).replace(',', '.'))
                    except: qta = 1.0
                    
                    p_unit = sanitize_value(p_unit)
                    qta = sanitize_value(qta)
                    tot_riga = sanitize_value(p_unit * qta)

                    # COSTRUZIONE RIGA (12 Colonne ora, inclusa Num Scontrino in L)
                    riga_completa = [
                        str(data_f),                        # A
                        str(insegna_f),                     # B
                        str(indirizzo_f),                   # C
                        str(row["Scontrino"]).upper(),      # D
                        tot_riga,                           # E
                        0,                                  # F
                        p_unit,                             # G
                        str(row["Offerta"]).upper(),        # H
                        qta,                                # I
                        "SI",                               # J
                        str(prod_id),                       # K (ID Prodotto)
                        str(num_scontrino_f)                # L (NUOVO: Numero Scontrino)
                    ]
                    rows_scontrini.append(riga_completa)

                # Scrittura su Google Sheets
                try:
                    if rows_catalogo_new:
                        ws_catalogo.append_rows(rows_catalogo_new, value_input_option='USER_ENTERED')
                    
                    if rows_scontrini:
                        ws_scontrini.append_rows(rows_scontrini, value_input_option='USER_ENTERED')
                        
                    st.success(f"✅ Salvataggio completato! Aggiunte {len(rows_scontrini)} righe.")
                    
                    # Reset e Ricarica
                    st.session_state.dati_analizzati = None
                    st.session_state.uploader_key += 1
                    time.sleep(1)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Errore scrittura Google: {e}")

# --- TAB 2: RICERCA (Logica Relazionale) ---
with tab_cerca:
    # Gestione Posizione
    if st.session_state.my_lat:
        st.success(f"📍 Posizione attiva")
        if st.button("🔄 Resetta Posizione"):
            st.session_state.my_lat = None; st.session_state.my_lon = None; st.rerun()
    else:
        with st.expander("📍 Imposta posizione", expanded=True):
            c_gps, c_man = st.columns([1, 2])
            with c_gps:
                if st.button("Usa GPS"):
                    loc = get_geolocation()
                    if loc:
                        st.session_state.my_lat = loc['coords']['latitude']
                        st.session_state.my_lon = loc['coords']['longitude']
                        st.rerun()
            with c_man:
                addr_in = st.text_input("Indirizzo o Città")
                if st.button("Cerca Indirizzo"):
                    lat, lon = get_coords_from_address(addr_in)
                    if lat: st.session_state.my_lat, st.session_state.my_lon = lat, lon; st.rerun()

    st.markdown("---")
    query = st.text_input("🔍 Cerca Prodotto (es. Latte, Tonno, Granarolo)", key="search_norm").upper().strip()
    
    if query:
        with st.spinner("Ricerca nel database normalizzato..."):
            try:
                data_scontrini = ws_scontrini.get_all_records()
                data_catalogo = ws_catalogo.get_all_records()
                
                if data_scontrini and data_catalogo:
                    df_s = pd.DataFrame(data_scontrini)
                    df_c = pd.DataFrame(data_catalogo)
                    
                    # Join Relazionale
                    df_s['ID_PRODOTTO'] = df_s['ID_PRODOTTO'].astype(str)
                    df_c['ID_PRODOTTO'] = df_c['ID_PRODOTTO'].astype(str)
                    df_full = pd.merge(df_s, df_c, on='ID_PRODOTTO', how='inner')
                    
                    # Filtro
                    mask = (
                        df_full['NOME_NORMALIZZATO'].str.contains(query, na=False) |
                        df_full['BRAND'].str.contains(query, na=False) |
                        df_full['CATEGORIA'].str.contains(query, na=False)
                    )
                    res = df_full[mask].copy()
                    
                    if not res.empty:
                        # Calcoli Prezzi
                        res['Prezzo_Unitario'] = res['Prezzo_Unitario'].apply(clean_price)
                        res['FORMATO'] = pd.to_numeric(res['FORMATO'], errors='coerce').fillna(1)
                        res['PREZZO_AL_L_KG'] = res['Prezzo_Unitario'] / res['FORMATO']
                        
                        # Calcolo Distanze
                        def add_dist(row):
                            if not st.session_state.my_lat: return 999
                            addr_clean = re.sub(r'\W+', '', str(row['Indirizzo'])).upper()
                            neg = next((n for n in lista_negozi_raw if re.sub(r'\W+', '', str(n.get('Indirizzo_Standard (Pulito)', ''))).upper() == addr_clean), None)
                            if neg and neg.get('Latitudine'):
                                try: return get_road_distance(st.session_state.my_lat, st.session_state.my_lon, float(str(neg['Latitudine']).replace(',','.')), float(str(neg['Longitudine']).replace(',','.')))
                                except: return 888
                            return 999

                        res['KM'] = res.apply(add_dist, axis=1)
                        res = res.sort_values(by=['PREZZO_AL_L_KG', 'KM'])
                        
                        # Top Result
                        best = res.iloc[0]
                        u = best['UNITA']
                        st.success(f"🏆 Best: **{best['NOME_NORMALIZZATO']}** a **{best['PREZZO_AL_L_KG']:.2f} €/{u}**")
                        st.caption(f"Presso {best['Negozio']} - {best['Data']}")
                        
                        # Table
                        show_cols = ['Data', 'NOME_NORMALIZZATO', 'Prezzo_Unitario', 'PREZZO_AL_L_KG', 'Negozio', 'Indirizzo', 'KM', 'In_Offerta']
                        renames = {'NOME_NORMALIZZATO': 'Prodotto', 'Prezzo_Unitario': 'Prezzo Conf.', 'PREZZO_AL_L_KG': f'Prezzo/{u}'}
                        
                        st.dataframe(
                            res[show_cols].rename(columns=renames), 
                            use_container_width=True, 
                            hide_index=True,
                            column_config={
                                f"Prezzo/{u}": st.column_config.NumberColumn(format="%.2f €"),
                                "Prezzo Conf.": st.column_config.NumberColumn(format="%.2f €"),
                                "KM": st.column_config.NumberColumn(format="%.1f km")
                            }
                        )
                    else: st.warning("Nessun prodotto trovato.")
                else: st.info("Database vuoto.")
            except Exception as e:
                st.error(f"Errore ricerca: {e}")
# --- TAB 3: CARRELLO OTTIMIZZATO (Multi-Stop & Highlighting) ---
with tab_carrello:
    
    # --- 1. SEZIONE GEOLOCALIZZAZIONE ---
    with st.expander("📍 Imposta la tua posizione", expanded=not st.session_state.my_lat):
        c_gps, c_man = st.columns([1, 2])
        with c_gps:
            if st.button("Usa GPS", key="gps_tab3"):
                loc = get_geolocation()
                if loc:
                    st.session_state.my_lat = loc['coords']['latitude']
                    st.session_state.my_lon = loc['coords']['longitude']
                    st.rerun()
        with c_man:
            addr_in = st.text_input("Oppure scrivi Città/Indirizzo", key="addr_input_tab3")
            if st.button("Cerca Indirizzo", key="addr_btn_tab3"):
                lat, lon = get_coords_from_address(addr_in)
                if lat: st.session_state.my_lat, st.session_state.my_lon = lat, lon; st.rerun()
        
        if st.session_state.my_lat:
            st.success("✅ Posizione impostata.")

    st.markdown("---")
    st.markdown("### 📝 La tua Lista della Spesa")

    if 'cart_input_text' not in st.session_state:
        st.session_state.cart_input_text = ""

    def clear_list(): st.session_state.cart_input_text = ""

    # INPUT AREA
    col_in, col_opt = st.columns([2, 1])
    with col_in:
        lista_input = st.text_area(
            "Scrivi i prodotti (uno per riga):", height=200, 
            placeholder="Latte\nUova\nTonno\nInsalata\nPane", key="cart_input_text"
        )
    
    with col_opt:
        max_dist_km = st.slider("Raggio (km)", 1, 100, 20)
        
        # --- NUOVO SELETTORE PER FRAZIONAMENTO ---
        stops_option = st.select_slider(
            "Max Negozi (Tappe)", 
            options=[1, 2, 3, "Illimitato"],
            value=1
        )
        st.caption("Aumenta le tappe per risparmiare di più.")
        
        st.write("") 
        b1, b2 = st.columns(2)
        with b1: btn_calc = st.button("🚀 Calcola", use_container_width=True, key="calc_tab3")
        with b2: st.button("🗑️ Svuota", on_click=clear_list, use_container_width=True, key="clear_tab3")
    
    if btn_calc:
        if not lista_input.strip():
            st.warning("Inserisci almeno un prodotto.")
        else:
            items = [x.strip().upper() for x in lista_input.split('\n') if x.strip()]
            
            with st.spinner(f"Ottimizzazione combinatoria per {len(items)} articoli..."):
                try:
                    # Caricamento e Pulizia DB (Standard)
                    data_s = ws_scontrini.get_all_records()
                    data_c = ws_catalogo.get_all_records()
                    if not data_s or not data_c: st.error("DB vuoto"); st.stop()

                    df_s = pd.DataFrame(data_s); df_c = pd.DataFrame(data_c)
                    df_s.columns = [c.strip() for c in df_s.columns]
                    df_c.columns = [c.strip() for c in df_c.columns]
                    df_s['ID_PRODOTTO'] = df_s['ID_PRODOTTO'].astype(str).str.strip()
                    df_c['ID_PRODOTTO'] = df_c['ID_PRODOTTO'].astype(str).str.strip()
                    
                    df_full = pd.merge(df_s, df_c, on='ID_PRODOTTO', how='inner')
                    df_full['Prezzo_Unitario'] = df_full['Prezzo_Unitario'].apply(clean_price)
                    
                    # Filtro Distanze
                    unique_shops = df_full[['Negozio', 'Indirizzo']].drop_duplicates()
                    shop_geo = {} # { "Negozio - Indirizzo": dist }
                    valid_shop_keys = []

                    for _, row in unique_shops.iterrows():
                        k = f"{row['Negozio']} - {row['Indirizzo']}"
                        if st.session_state.my_lat:
                            addr_clean = re.sub(r'\W+', '', str(row['Indirizzo'])).upper()
                            neg = next((n for n in lista_negozi_raw if re.sub(r'\W+', '', str(n.get('Indirizzo_Standard (Pulito)', ''))).upper() == addr_clean), None)
                            dist = 999
                            if neg and neg.get('Latitudine'):
                                try: dist = get_road_distance(st.session_state.my_lat, st.session_state.my_lon, float(str(neg['Latitudine']).replace(',','.')), float(str(neg['Longitudine']).replace(',','.')))
                                except: dist = 888
                        else: dist = 0
                        
                        shop_geo[k] = dist
                        if dist <= max_dist_km: valid_shop_keys.append(k)
                    
                    if not valid_shop_keys: st.warning("Nessun negozio nel raggio."); st.stop()

                    # --- CREAZIONE MATRICE PREZZI ---
                    # Struttura: { 'LATTE': { 'Shop A': (0.90, 'Latte Granarolo'), 'Shop B': ... } }
                    price_matrix = {item: {} for item in items}
                    
                    for item in items:
                        # Ricerca
                        mask = (df_full['NOME_NORMALIZZATO'].str.contains(item, na=False) | df_full['CATEGORIA'].str.contains(item, na=False))
                        df_item = df_full[mask]
                        if df_item.empty: continue
                        
                        # Trova miglior prezzo per ogni negozio valido
                        for shop_key in valid_shop_keys:
                            neg, ind = shop_key.split(' - ', 1)
                            sub = df_item[(df_item['Negozio'] == neg) & (df_item['Indirizzo'] == ind)]
                            if not sub.empty:
                                best_row = sub.loc[sub['Prezzo_Unitario'].idxmin()]
                                price_matrix[item][shop_key] = (best_row['Prezzo_Unitario'], best_row['NOME_NORMALIZZATO'])

                    # --- ALGORITMO DI OTTIMIZZAZIONE COMBINATORIA ---
                    # 1. Calcolo Vincitore Singolo (Tappa = 1)
                    best_single_shop = None
                    best_single_total = float('inf')
                    single_results = []

                    for shop in valid_shop_keys:
                        tot = 0
                        found = 0
                        missing_count = 0
                        for item in items:
                            if shop in price_matrix[item]:
                                tot += price_matrix[item][shop][0]
                                found += 1
                            else:
                                missing_count += 1
                        
                        single_results.append({
                            'Negozio': shop, 'Totale': tot, 'Trovati': found, 
                            'Missing': missing_count, 'Distanza': shop_geo[shop]
                        })
                        
                        # Logica "Vincitore": Più prodotti trovati, poi prezzo minore
                        # Penalizziamo pesantemente chi ha prodotti mancanti per il sorting
                        score = (missing_count * 10000) + tot 
                        if score < best_single_total and found > 0:
                            best_single_total = score
                            best_single_shop = shop

                    # Ordiniamo la classifica singola
                    df_res = pd.DataFrame(single_results).sort_values(by=['Missing', 'Totale']).reset_index(drop=True)
                    winner_single = df_res.iloc[0] if not df_res.empty else None

                    # 2. Calcolo Multistop (Se richiesto)
                    best_combo = None
                    best_combo_total = float('inf')
                    best_combo_details = {} # {Item: (Price, ShopKey, Name)}

                    if stops_option == 1:
                        # Se l'utente vuole 1 stop, il "combo" è il vincitore singolo
                        target_combo = [winner_single['Negozio']] if winner_single is not None else []
                    elif stops_option == "Illimitato":
                        # Mix puro: prende il minimo ovunque
                        target_combo = valid_shop_keys # Consideriamo tutti i negozi come potenziali
                    else:
                        # Combinazioni: 2 o 3 negozi
                        # Genera combinazioni di dimensione 'stops_option'
                        # Filtriamo negozi che hanno almeno 1 prodotto per non esplodere
                        candidate_shops = [r['Negozio'] for r in single_results if r['Trovati'] > 0]
                        target_combo = None # Sarà calcolato nel loop
                    
                    # LOGICA CORE MULTI-STOP
                    if stops_option != 1:
                        # Se è illimitato, non iteriamo combinazioni, facciamo one-pass su tutti
                        combinations = [valid_shop_keys] if stops_option == "Illimitato" else itertools.combinations(candidate_shops, stops_option)
                        
                        for combo in combinations:
                            current_combo_tot = 0
                            current_combo_map = {}
                            missing_in_combo = 0
                            
                            for item in items:
                                # Trova il prezzo minimo per questo item TRA I NEGOZI DELLA COMBO
                                min_p = float('inf')
                                best_s = None
                                best_n = ""
                                
                                found_in_this_combo = False
                                for shop in combo:
                                    if shop in price_matrix[item]:
                                        p, n = price_matrix[item][shop]
                                        if p < min_p:
                                            min_p = p
                                            best_s = shop
                                            best_n = n
                                        found_in_this_combo = True
                                
                                if found_in_this_combo:
                                    current_combo_tot += min_p
                                    current_combo_map[item] = (min_p, best_s, best_n)
                                else:
                                    missing_in_combo += 1
                            
                            # Valutazione
                            score = (missing_in_combo * 10000) + current_combo_tot
                            if score < best_combo_total:
                                best_combo_total = score
                                best_combo_details = current_combo_map
                                best_combo = combo

                    # --- VISUALIZZAZIONE RISULTATI ---
                    
                    # A. BOX PRINCIPALE (Il piano d'azione)
                    if stops_option == 1:
                        st.success(f"🏆 VINCITORE (Tappa Unica): **{winner_single['Negozio'].split(' - ')[0]}**")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Totale", f"€ {winner_single['Totale']:.2f}")
                        c2.metric("Prodotti", f"{winner_single['Trovati']}/{len(items)}")
                        c3.metric("Distanza", f"{winner_single['Distanza']} km")
                        
                        # Dettaglio semplice
                        with st.expander("📝 Vedi lista spesa", expanded=True):
                            shop = winner_single['Negozio']
                            for item in items:
                                if shop in price_matrix[item]:
                                    p, n = price_matrix[item][shop]
                                    st.markdown(f"✅ **{item}**: € {p:.2f} <span style='color:grey'>({n})</span>", unsafe_allow_html=True)
                                else:
                                    st.markdown(f"❌ **{item}**: _Non disponibile_", unsafe_allow_html=True)

                    else:
                        # Visualizzazione Multi-Stop Ottimizzata
                        real_total = sum([v[0] for v in best_combo_details.values()])
                        found_count = len(best_combo_details)
                        
                        risparmio = ""
                        if winner_single is not None:
                             diff = winner_single['Totale'] - real_total
                             if diff > 0.1: risparmio = f"(Risparmi € {diff:.2f} rispetto alla spesa unica)"
                        
                        st.info(f"⚡ PIANO OTTIMIZZATO ({stops_option if stops_option != 'Illimitato' else 'MAX'} TAPPE)")
                        
                        c1, c2 = st.columns(2)
                        c1.metric("Totale Ottimizzato", f"€ {real_total:.2f}")
                        c2.caption(risparmio)

                        st.markdown("##### 🛒 Lista della spesa divisa:")
                        
                        # Raggruppiamo per negozio per stampare ordinato
                        # Invertiamo la mappa: Shop -> [Items]
                        shop_bucket = {}
                        for item, (p, s, n) in best_combo_details.items():
                            if s not in shop_bucket: shop_bucket[s] = []
                            shop_bucket[s].append((item, p, n))
                        
                        # Stile per evidenziare indirizzo e negozio
                        for shop, goods in shop_bucket.items():
                            neg_name, neg_addr = shop.split(' - ', 1)
                            # Calcolo subtotale per negozio
                            subtot = sum([g[1] for g in goods])
                            
                            # BOX GIALLO/NERO PER EVIDENZIARE (Richiesta Utente)
                            st.markdown(
                                f"""
                                <div style="background-color: #262730; border: 1px solid #FFD700; padding: 10px; border-radius: 5px; margin-bottom: 10px;">
                                    <h4 style="color: #FFD700; margin:0;">🏪 {neg_name}</h4>
                                    <p style="font-size: 12px; color: #cccccc; margin:0;">📍 {neg_addr} ({shop_geo[shop]} km)</p>
                                    <p style="font-weight: bold; margin-top:5px;">Da prendere qui (Tot: € {subtot:.2f}):</p>
                                </div>
                                """, 
                                unsafe_allow_html=True
                            )
                            
                            for item, p, n in goods:
                                st.markdown(f"- **{item}**: € {p:.2f} <span style='color:grey'>({n})</span>", unsafe_allow_html=True)
                        
                        # Articoli mancanti ovunque
                        if found_count < len(items):
                            st.error(f"❌ Articoli non trovati in nessun negozio: {len(items) - found_count}")


                    # B. CLASSIFICA SINGOLA (Sempre utile come riferimento)
                    st.markdown("---")
                    st.markdown("### 📊 Classifica Negozi Singoli (Se non vuoi girare)")
                    for index, row in df_res.iterrows():
                        shop_name = row['Negozio']
                        totale = row['Totale']
                        trovati = row['Trovati']
                        distanza = row['Distanza']
                        label = f"#{index+1} | € {totale:.2f} | {trovati}/{len(items)} art. | {distanza} km | {shop_name}"
                        with st.expander(label):
                            for item in items:
                                if shop_name in price_matrix[item]:
                                    p, n = price_matrix[item][shop_name]
                                    st.markdown(f"✅ **{item}**: € {p:.2f} <span style='color:grey'>({n})</span>", unsafe_allow_html=True)
                                else:
                                    st.markdown(f"❌ **{item}**: _Non disponibile_", unsafe_allow_html=True)

                except Exception as e:
                    st.error(f"Errore tecnico: {e}")
