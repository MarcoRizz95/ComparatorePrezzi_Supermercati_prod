import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
import os
import json

def run_cleanup():
    try:
        print("Inizio procedura di pulizia...")
        # Recupero credenziali
        info = json.loads(os.environ['GOOGLE_SHEETS_JSON'])
        creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        gc = gspread.authorize(creds)
        
        # Apertura foglio
        sh = gc.open("Database_Prezzi")
        worksheet = sh.get_worksheet(0)
        
        # Caricamento dati
        data = worksheet.get_all_records()
        if not data:
            print("Database vuoto.")
            return
            
        df = pd.DataFrame(data)
        original_rows = len(df)
        
        # Pulizia: consideriamo duplicata una riga con stessa Data, Negozio, Prodotto e Prezzo
        # Usiamo nomi generici per trovare le colonne anche se hanno spazi
        df.columns = [c.strip() for c in df.columns]
        subset_cols = [c for c in df.columns if c in ['Data', 'Supermercato', 'Indirizzo', 'Prodotto', 'Prezzo_Netto', 'Prezzo Un.']]
        
        df_pulito = df.drop_duplicates(subset=subset_cols, keep='last')
        
        if len(df_pulito) < original_rows:
            # Svuota tutto
            worksheet.clear()
            # Prepara i dati per il caricamento (Intestazioni + Righe)
            nuovi_dati = [df_pulito.columns.values.tolist()] + df_pulito.values.tolist()
            # Scrittura (usando il metodo più compatibile)
            worksheet.update('A1', nuovi_dati)
            print(f"✅ Successo! Rimosse {original_rows - len(df_pulito)} righe duplicate.")
        else:
            print("✨ Nessun duplicato trovato.")
            
    except Exception as e:
        print(f"❌ Errore: {e}")
        raise e

if __name__ == "__main__":
    run_cleanup()
