import pandas as pd
from sqlmodel import Session, select, SQLModel
from app.database import engine
from app.models import QuranVerse  # Pakai model dari app.models

file_path = "surah.csv"

def buat_tabel_dan_seed():
    print("🔄 Membuat tabel quran_verses di database (jika belum ada)...")
    SQLModel.metadata.create_all(engine)
    
    print(f"🔄 Lagi membaca file lokal {file_path}...")
    try:
        df = pd.read_csv(file_path)
        
        # Cleaning data NaN/Kosong dari CSV
        df['tafsir_wajiz'] = df['tafsir_wajiz'].fillna('Tidak ada tafsir.')
        df['arabic'] = df['arabic'].fillna('')
        df['translation'] = df['translation'].fillna('')
        df['page'] = df['page'].fillna(1).astype(int)
        df['juz'] = df['juz'].fillna(1).astype(int)
        
        total_rows = len(df)
        print(f"✅ File berhasil dibaca! Menemukan {total_rows} ayat Al-Quran.")
        print("🚀 Memulai proses seeding ke database Postgres, tunggu bentar...")
        
        with Session(engine) as session:
            # Cek dulu biar gak double seed jika pernah dijalankan
            cek_data = session.exec(select(QuranVerse)).first()
            if cek_data:
                print("⚠️ Database udah ada isinya! Seeding dibatalkan biar gak double.")
                return

            for index, row in df.iterrows():
                # Mapping data dari CSV ke Model QuranVerse
                data_ayat = QuranVerse(
                    surah_id=int(row['surah_id']),
                    surah_name=str(row['surah_latin']).strip(),
                    ayah_number=int(row['ayah']),
                    page_number=int(row['page']),  # Mapping Halaman Mushaf (1-604)
                    juz_number=int(row['juz']),    # Mapping Juz (1-30)
                    text_arabic=str(row['arabic']),
                    text_id=str(row['translation']),
                    tafsir_wajiz=str(row['tafsir_wajiz'])
                )
                session.add(data_ayat)
                
                # Commit bertahap per 500 baris
                if index % 500 == 0 and index > 0:
                    session.commit()
                    print(f"▓ {index}/{total_rows} ayat berhasil masuk database...")
            
            # Commit sisa data
            session.commit()
            print("\n🎉 KELAR! Semua ayat beserta Halaman & Juz udah masuk ke Postgres!")

    except Exception as e:
        print("Waduh gagal, error-nya:", e)

if __name__ == "__main__":
    buat_tabel_dan_seed()