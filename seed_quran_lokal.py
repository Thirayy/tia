import pandas as pd
import numpy as np
from sqlmodel import Session, select, SQLModel, Field, create_engine
from app.database import engine, get_session # Pastiin file database.py ada engine-nya

# ==========================================
# 1. DEFINISI MODEL TABEL QURAN LENGKAP
# ==========================================
class QuranVerse(SQLModel, table=True):
    __tablename__ = "quran_verses"
    
    id: int = Field(default=None, primary_key=True)
    surah_id: int
    surah_name: str
    ayah_number: int
    text_arabic: str
    text_id: str
    tafsir_wajiz: str

file_path = "surah.csv"

def buat_tabel_dan_seed():
    print("🔄 Membuat tabel quran_verses di database (jika belum ada)...")
    SQLModel.metadata.create_all(engine)
    
    print("🔄 Lagi membaca file lokal surah.csv (47MB)...")
    try:
        df = pd.read_csv(file_path)
        
        # Bersihin data NaN/Kosong dari CSV biar gak bikin crash database
        df['tafsir_wajiz'] = df['tafsir_wajiz'].fillna('Tidak ada tafsir.')
        df['arabic'] = df['arabic'].fillna('')
        df['translation'] = df['translation'].fillna('')
        
        total_rows = len(df)
        print(f"✅ File berhasil dibaca! Menemukan {total_rows} ayat Al-Quran.")
        print("🚀 Memulai proses seeding ke database Postgres, tunggu bentar...")
        
        with Session(engine) as session:
            # Cek dulu biar gak double seed kalau gak sengaja jalanin dua kali
            cek_data = session.exec(select(QuranVerse)).first()
            if cek_data:
                print("⚠️ Database udah ada isinya! Seeding dibatalkan biar gak double.")
                return

            for index, row in df.iterrows():
                # Mapping data dari kolom CSV asli Hugging Face ke Model DB kita
                data_ayat = QuranVerse(
                    surah_id=int(row['surah_id']),
                    surah_name=str(row['surah_latin']),
                    ayah_number=int(row['ayah']),
                    text_arabic=str(row['arabic']),
                    text_id=str(row['translation']),
                    tafsir_wajiz=str(row['tafsir_wajiz'])
                )
                session.add(data_ayat)
                
                # Commit bertahap per 500 baris biar RAM laptop gak meledak
                if index % 500 == 0 and index > 0:
                    session.commit()
                    print(f"▓ {index}/{total_rows} ayat berhasil masuk database...")
            
            # Commit sisa datanya
            session.commit()
            print("\n🎉  KELAR! Semua 6.236 ayat beserta Tafsir Kemenag udah masuk ke Postgres!")

    except Exception as e:
        print("Waduh gagal , error-nya:", e)

if __name__ == "__main__":
    buat_tabel_dan_seed()