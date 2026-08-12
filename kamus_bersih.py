import pandas as pd
from sqlmodel import Session, select, SQLModel
from app.database import engine
from app.models import SurahModel  # Ganti dengan nama model tabel surah di app.models

file_path = "kamus_surah_bersih.csv"

def buat_tabel_dan_seed_surah():
    print("🔄 Membuat tabel surah di database (jika belum ada)...")
    SQLModel.metadata.create_all(engine)
    
    print(f"🔄 Lagi membaca file lokal {file_path}...")
    try:
        df = pd.read_csv(file_path)
        
        # Cleaning data NaN/Kosong dari CSV
        df['nama_lain'] = df['nama_lain'].fillna('')
        df['typo_asr'] = df['typo_asr'].fillna('')
        
        total_rows = len(df)
        print(f"✅ File berhasil dibaca! Menemukan {total_rows} data surah.")
        print("🚀 Memulai proses seeding ke database Postgres, tunggu bentar...")
        
        with Session(engine) as session:
            # Cek dulu biar gak double seed jika pernah dijalankan
            cek_data = session.exec(select(SurahModel)).first()
            if cek_data:
                print("⚠️ Database udah ada isinya! Seeding dibatalkan biar gak double.")
                return

            for index, row in df.iterrows():
                # Mapping data dari CSV ke Model SurahModel
                data_surah = SurahModel(
                    id_surah=int(row['id_surah']),
                    nama_surah=str(row['nama_surah']).strip(),
                    nama_lain=str(row['nama_lain']),
                    typo_asr=str(row['typo_asr'])
                )
                session.add(data_surah)
                
                # Commit bertahap per 50 baris (karena data surah cuma 114)
                if index % 50 == 0 and index > 0:
                    session.commit()
                    print(f"▓ {index}/{total_rows} data surah berhasil masuk database...")
            
            # Commit sisa data terakhir
            session.commit()
            print("\n🎉 KELAR! Semua data surah beserta nama lain & typo asr udah masuk ke Postgres!")

    except Exception as e:
        print("Waduh gagal, error-nya:", e)

if __name__ == "__main__":
    buat_tabel_dan_seed_surah()