import pandas as pd
from sqlmodel import Session, SQLModel
from app.database import engine
from app.models import QuranKnowledge

# Dataset Terminologi, Parameter Tajwid, dan Evaluasi Tahfizh Lengkap
KNOWLEDGE_DATA = [
    # --- TAJWID: HUKUM NUN MATI & TANWIN ---
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Nun Mati & Tanwin",
        "Nama_Parameter": "Idzhar Halqi",
        "Referensi_Ayat": "QS. Al-Baqarah: 6",
        "Teks_Mentah_Penjelasan": "Membaca Nun sukun (نْ) atau tanwin dengan jelas dan terang tanpa dengung (ghunnah) apabila bertemu dengan salah satu dari 6 huruf tenggorokan: Hamzah (ء), Ha (هـ), 'Ain (ع), Ha (ح), Ghain (غ), Kha (خ)."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Nun Mati & Tanwin",
        "Nama_Parameter": "Idgham Bighunnah",
        "Referensi_Ayat": "QS. Al-Lahab: 1",
        "Teks_Mentah_Penjelasan": "Memasukkan suara Nun sukun atau tanwin ke dalam huruf berikutnya disertai dengung selama 2 harakat jika bertemu huruf Ya (ي), Nun (ن), Mim (م), Wawu (و)."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Nun Mati & Tanwin",
        "Nama_Parameter": "Idgham Bilaghunnah",
        "Referensi_Ayat": "QS. Al-Ikhlas: 4",
        "Teks_Mentah_Penjelasan": "Memasukkan suara Nun sukun atau tanwin ke dalam huruf Lam (ل) atau Ra (ر) secara sempurna TANPA disertai dengung."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Nun Mati & Tanwin",
        "Nama_Parameter": "Iqlab",
        "Referensi_Ayat": "QS. Al-Baqarah: 18",
        "Teks_Mentah_Penjelasan": "Mengubah suara Nun sukun atau tanwin menjadi suara Mim (م) yang samar disertai dengung 2 harakat ketika bertemu huruf Ba (ب)."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Nun Mati & Tanwin",
        "Nama_Parameter": "Ikhfa Haqiqi",
        "Referensi_Ayat": "QS. Al-Falaq: 2",
        "Teks_Mentah_Penjelasan": "Menyamarkan suara Nun sukun atau tanwin antara bacaan Idzhar dan Idgham disertai dengung jika bertemu 15 huruf Ikhfa (ت, ث, ج, د, ذ, ز, س, ش, ص, ض, ط, ظ, ف, ق, ك)."
    },

    # --- TAJWID: HUKUM MIM MATI ---
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Mim Mati",
        "Nama_Parameter": "Ikhfa Syafawi",
        "Referensi_Ayat": "QS. Al-Fil: 4",
        "Teks_Mentah_Penjelasan": "Menyamarkan suara Mim sukun (مْ) disertai dengung di kedua bibir apabila bertemu dengan huruf Ba (ب)."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Mim Mati",
        "Nama_Parameter": "Idgham Mutamatsilain / Idgham Mimi",
        "Referensi_Ayat": "QS. Quraysh: 4",
        "Teks_Mentah_Penjelasan": "Memasukkan suara Mim sukun ke dalam huruf Mim berikutnya sehingga menjadi bertasydid dan didengungkan 2 harakat."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Mim Mati",
        "Nama_Parameter": "Idzhar Syafawi",
        "Referensi_Ayat": "QS. Al-Fatihah: 7",
        "Teks_Mentah_Penjelasan": "Membaca Mim sukun dengan jelas di bibir tanpa dengung apabila bertemu seluruh huruf hijaiyah selain Ba (ب) dan Mim (م)."
    },

    # --- TAJWID: HUKUM MAD ---
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Mad",
        "Nama_Parameter": "Mad Thabi'i (Mad Asli)",
        "Referensi_Ayat": "QS. Al-Fatihah: 1",
        "Teks_Mentah_Penjelasan": "Membaca panjang 2 harakat pada huruf Alif setelah fathah, Wawu sukun setelah dhammad, atau Ya sukun setelah kasrah."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Mad",
        "Nama_Parameter": "Mad Wajib Muttashil",
        "Referensi_Ayat": "QS. An-Nasr: 1",
        "Teks_Mentah_Penjelasan": "Mad Thabi'i bertemu dengan Hamzah (ء) dalam SATU KATA. Panjang bacaan adalah 4 atau 5 harakat."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Mad",
        "Nama_Parameter": "Mad Jaiz Munfashil",
        "Referensi_Ayat": "QS. Al-Kautsar: 1",
        "Teks_Mentah_Penjelasan": "Mad Thabi'i bertemu dengan Hamzah (ء) pada KATA YANG BERBEDA. Panjang bacaan dibaca 2, 4, atau 5 harakat."
    },
    {
        "Kategori": "Tajwid",
        "Sub_Kategori": "Hukum Mad",
        "Nama_Parameter": "Mad 'Arid Lissukun",
        "Referensi_Ayat": "QS. Al-Fatihah: 2",
        "Teks_Mentah_Penjelasan": "Mad Thabi'i bertemu huruf hijaiyah hidup yang dibaca sukun karena diwakafkan (berhenti). Panjangnya 2, 4, atau 6 harakat."
    },

    # --- EVALUASI TAHFIZH & KELANCARAN SETORAN ---
    {
        "Kategori": "Evaluasi Tahfizh",
        "Sub_Kategori": "Kelancaran Setoran",
        "Nama_Parameter": "Lupa Ayat / Terhenti (Tawaqquf)",
        "Referensi_Ayat": "QS. Al-A'la: 6",
        "Teks_Mentah_Penjelasan": "Kondisi di mana santri terhenti membaca lebih dari 3-5 detik. Penguji memberikan pancingan (fath) maksimal 2-3 kata awal ayat."
    },
    {
        "Kategori": "Evaluasi Tahfizh",
        "Sub_Kategori": "Kelancaran Setoran",
        "Nama_Parameter": "Ayat Tertukar (Tasyabuh)",
        "Referensi_Ayat": "QS. Al-Kafirun: 3-5",
        "Teks_Mentah_Penjelasan": "Kesalahan di mana santri melompat atau berpindah ke ayat/surah lain yang memiliki kemiripan bunyi (mutasyabihat)."
    },
    {
        "Kategori": "Evaluasi Tahfizh",
        "Sub_Kategori": "Kelancaran Setoran",
        "Nama_Parameter": "Salah Harakat (Lahn Jali)",
        "Referensi_Ayat": "QS. Al-Fatihah: 7",
        "Teks_Mentah_Penjelasan": "Kesalahan fatal berupa perubahan harakat atau huruf yang dapat merubah arti ayat. Harus segera dikoreksi oleh penguji."
    },

    # --- MAKHRAJ HURUF ---
    {
        "Kategori": "Makhraj Huruf",
        "Sub_Kategori": "Al-Halq (Tenggorokan)",
        "Nama_Parameter": "Aqshal Halq (Tenggorokan Bawah)",
        "Referensi_Ayat": "QS. Al-Ikhlas: 1",
        "Teks_Mentah_Penjelasan": "Tempat keluar huruf Hamzah (ء) dan Ha (هـ) dari pangkal tenggorokan paling dalam dekat dada."
    },
    {
        "Kategori": "Makhraj Huruf",
        "Sub_Kategori": "Al-Halq (Tenggorokan)",
        "Nama_Parameter": "Washat Halq (Tenggorokan Tengah)",
        "Referensi_Ayat": "QS. Al-Fatihah: 1",
        "Teks_Mentah_Penjelasan": "Tempat keluar huruf 'Ain (ع) dan Ha (ح) dari bagian tengah tenggorokan."
    }
]

def build_and_seed_knowledge():
    # 1. Simpan ke file CSV
    df = pd.DataFrame(KNOWLEDGE_DATA)
    csv_file = "dataset_knowledge.csv"
    df.to_csv(csv_file, index=False)
    print(f"📦 CSV berhasil dibuat: {csv_file}")

    # 2. Buat Tabel di Postgres & Seed Data
    print("🔄 Membuat tabel quran_knowledge di Postgres...")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        # Kosongkan tabel jika ingin merefresh data
        for row in KNOWLEDGE_DATA:
            knowledge = QuranKnowledge(
                kategori=row["Kategori"],
                sub_kategori=row["Sub_Kategori"],
                nama_parameter=row["Nama_Parameter"],
                referensi_ayat=row["Referensi_Ayat"],
                teks_mentah_penjelasan=row["Teks_Mentah_Penjelasan"]
            )
            session.add(knowledge)
        
        session.commit()
        print(f"🎉 SUCCESS! {len(KNOWLEDGE_DATA)} data terminologi/parameter berhasil di-seed ke tabel quran_knowledge!")

if __name__ == "__main__":
    build_and_seed_knowledge()