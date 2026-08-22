import pandas as pd
from sqlmodel import Session, select, SQLModel
from app.database import engine
from app.models import TajwidModel  # Sesuaikan dengan nama model tabel tajwid di app.models Anda

file_path = "kamus_tajwid_bersih.csv"

def buat_tabel_dan_seed_tajwid():
    print("🔄 Membuat tabel kaidah tajwid di database (jika belum ada)...")
    SQLModel.metadata.create_all(engine)
    
    print(f"🔄 Lagi membaca file lokal {file_path}...")
    try:
        df = pd.read_csv(file_path)
        
        # Cleaning data NaN/Kosong dari CSV
        df['kategori_utama'] = df['kategori_utama'].fillna('')
        df['sub_kaidah'] = df['sub_kaidah'].fillna('')
        df['keterangan_resmi'] = df['keterangan_resmi'].fillna('')
        df['kriteria_penilaian'] = df['kriteria_penilaian'].fillna('')
        df['nama_lain'] = df['nama_lain'].fillna('')
        df['typo_asr'] = df['typo_asr'].fillna('')
        
        total_rows = len(df)
        print(f"✅ File berhasil dibaca! Menemukan {total_rows} data kaidah tajwid.")
        print("🚀 Memulai proses seeding ke database Postgres, tunggu bentar...")
        
        with Session(engine) as session:
            # Cek dulu biar gak double seed jika pernah dijalankan
            cek_data = session.exec(select(TajwidModel)).first()
            if cek_data:
                print("⚠️ Database udah ada isinya! Seeding dibatalkan biar gak double.")
                return

            for index, row in df.iterrows():
                # Mapping data dari CSV ke Model TajwidModel
                data_tajwid = TajwidModel(
                    id_kaidah=int(row['id_kaidah']),
                    kategori_utama=str(row['kategori_utama']).strip(),
                    sub_kaidah=str(row['sub_kaidah']).strip(),
                    keterangan_resmi=str(row['keterangan_resmi']).strip(),
                    kriteria_penilaian=str(row['kriteria_penilaian']).strip(),
                    nama_lain=str(row['nama_lain']).strip(),
                    typo_asr=str(row['typo_asr']).strip()
                )
                session.add(data_tajwid)
                
                # Commit bertahap per 10 baris (karena total data tajwid ada 33 kaidah)
                if index % 10 == 0 and index > 0:
                    session.commit()
                    print(f"▓ {index}/{total_rows} data tajwid berhasil masuk database...")
            
            # Commit sisa data terakhir
            session.commit()
            print("\n🎉 KELAR! Semua data kaidah tajwid lengkap dengan nama lain & typo asr udah masuk ke Postgres!")

    except Exception as e:
        print("Waduh gagal, error-nya:", e)

if __name__ == "__main__":
    buat_tabel_dan_seed_tajwid()