import os
import io
import re
import json
import datetime
from pathlib import Path
from flask import Flask, request, render_template, jsonify, send_file, session, redirect, url_for
from openai import OpenAI
import mysql.connector
import PyPDF2
import docx

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch

app = Flask(__name__)
app.secret_key = "stk-ai-checker-secret-2026"

from datetime import timedelta

app.permanent_session_lifetime = timedelta(minutes=30)

APP_PASSWORD = "stkmerauke01"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ================= AUTO CLEANUP REPORTS (1x per hari) =================

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "laporan"
CLEANUP_TRACK_FILE = BASE_DIR / "last_cleanup.txt"

def cleanup_old_reports():
    folder = REPORTS_DIR

    if not folder.exists():
        return

    today = datetime.date.today()

    # Cek apakah sudah cleanup hari ini
    if CLEANUP_TRACK_FILE.exists():
        try:
            with CLEANUP_TRACK_FILE.open("r") as f:
                last_cleanup_date = f.read().strip()
                if last_cleanup_date == str(today):
                    return  # Sudah cleanup hari ini
        except:
            pass

    now = datetime.datetime.now()
    expire_days = 30

    for filename in os.listdir(folder):
        filepath = folder / filename

        if filepath.is_file():
            try:
                file_modified_time = datetime.datetime.fromtimestamp(filepath.stat().st_mtime)
                age_days = (now - file_modified_time).days

                if age_days > expire_days:
                    filepath.unlink()
            except Exception as e:
                print("Gagal hapus file:", filepath, e)

    # Simpan tanggal cleanup terakhir
    try:
        with CLEANUP_TRACK_FILE.open("w") as f:
            f.write(str(today))
    except:
        pass


@app.before_request
def auto_cleanup():
    cleanup_old_reports()

# ================= DATABASE =================
db_config = {
    "host": "localhost",
    "user": "stkp7133_stkyakob_aiadmin",
    "password": "@stkmerauke01",
    "database": "stkp7133_stkyakob_aichecker"
}

# ================= TEXT EXTRACTION =================
def ekstrak_teks(file):
    if not file or not file.filename:
        raise ValueError("File proposal wajib diunggah")

    teks = ""
    filename = file.filename.lower()
    file_stream = file.read()

    if not file_stream:
        raise ValueError("File proposal kosong")

    if filename.endswith(".pdf"):
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_stream))
        for page in pdf_reader.pages:
            if page.extract_text():
                teks += page.extract_text() + "\n"

    elif filename.endswith(".docx"):
        doc = docx.Document(io.BytesIO(file_stream))
        for para in doc.paragraphs:
            teks += para.text + "\n"
    else:
        raise ValueError("Format file tidak didukung. Gunakan PDF atau DOCX")

    return teks.strip()

# ================= CHUNKING =================
def chunk_paragraphs(paragraphs, max_chars=12000):
    chunks = []
    current = []
    total = 0

    for p in paragraphs:
        if total + len(p) > max_chars:
            chunks.append(current)
            current = []
            total = 0
        current.append(p)
        total += len(p)

    if current:
        chunks.append(current)

    return chunks

# ================= RISK CATEGORY =================
def kategori_risiko(skor):
    if skor >= 75:
        return "high"
    elif skor >= 50:
        return "moderate"
    else:
        return "low"

# ================= PDF EXECUTIVE =================
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import A4

def generate_pdf(nama_mahasiswa, skor_total, stats, highlight_map, paragraphs):
    REPORTS_DIR.mkdir(exist_ok=True)

    safe_nama = re.sub(r"[^A-Za-z0-9_-]+", "_", (nama_mahasiswa or "mahasiswa").strip())
    filename = REPORTS_DIR / f"laporan_{safe_nama}_{int(datetime.datetime.now().timestamp())}.pdf"

    doc = SimpleDocTemplate(str(filename), pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # ================= HEADER =================
    elements.append(Paragraph("<b>SEKOLAH TINGGI KATOLIK SANTO YAKOBUS MERAUKE</b>", styles['Heading2']))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(Paragraph("<b>LAPORAN ANALISIS PROBABILISTIC AI PATTERN</b>", styles['Heading1']))
    elements.append(Spacer(1, 0.3 * inch))

    # ================= IDENTITAS =================
    elements.append(Paragraph(f"<b>Nama Mahasiswa:</b> {nama_mahasiswa}", styles['Normal']))
    elements.append(Paragraph(f"<b>Skor Total AI Probability:</b> {skor_total}%", styles['Normal']))
    elements.append(Spacer(1, 0.3 * inch))

    # ================= DISTRIBUSI TABLE =================
    table_data = [
        ["Kategori", "Jumlah Paragraf"],
        ["High Risk (≥75%)", stats["high"]],
        ["Moderate (50–74%)", stats["moderate"]],
        ["Low (<50%)", stats["low"]],
        ["Total Paragraf", stats["total"]],
    ]

    table = Table(table_data, colWidths=[250, 150])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('ALIGN',(1,1),(-1,-1),'CENTER'),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 0.4 * inch))

    # ================= HIGHLIGHT SUMMARY =================
    elements.append(Paragraph("<b>Ringkasan Paragraf Risiko Tinggi & Sedang:</b>", styles['Heading3']))
    elements.append(Spacer(1, 0.2 * inch))

    for idx, item in highlight_map.items():
        skor = item["skor"]
        if skor >= 50:
            cuplikan = paragraphs[idx][:400]
            elements.append(Paragraph(
                f"<b>Paragraf {idx+1} (Skor: {skor}%)</b>",
                styles['Normal']
            ))
            elements.append(Paragraph(cuplikan + "...", styles['Normal']))
            elements.append(Paragraph(
                f"<i>Alasan:</i> {item['alasan']}",
                styles['Italic']
            ))
            elements.append(Spacer(1, 0.3 * inch))

    # ================= INTERPRETATION GUIDE =================
    elements.append(Paragraph("<b>Interpretation Guide:</b>", styles['Heading3']))
    elements.append(Paragraph("• < 40% → Kemungkinan besar Human-written", styles['Normal']))
    elements.append(Paragraph("• 40–70% → Kemungkinan Campuran", styles['Normal']))
    elements.append(Paragraph("• > 70% → Perlu Klarifikasi Akademik", styles['Normal']))
    elements.append(Spacer(1, 0.3 * inch))

    # ================= DISCLAIMER =================
    elements.append(Paragraph(
        "Hasil analisis ini bersifat probabilistik dan bukan bukti definitif penggunaan AI. "
        "Digunakan sebagai alat bantu diskusi akademik antara dosen dan mahasiswa sebelum seminar proposal.",
        styles['Italic']
    ))
    elements.append(Spacer(1, 0.3 * inch))

    elements.append(Paragraph(
        f"Tanggal Analisis: {datetime.datetime.now().strftime('%d %B %Y %H:%M')}",
        styles['Normal']
    ))

    doc.build(elements)
    return filename.name

# ================= ROUTES =================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")

        if password == APP_PASSWORD:
            session.permanent = True
            session["authenticated"] = True
            return redirect(url_for("index"))
        else:
            return render_template("login.html", error="Password salah")

    return render_template("login.html")
    
@app.route("/")
def index():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return render_template("index.html")

@app.route("/cek-ai", methods=["POST"])
def cek_ai():
    if not session.get("authenticated"):
        return jsonify({"status": "error", "pesan": "Unauthorized"})
        
    try:
        nama_penguji = request.form.get("nama_penguji")
        nama_mahasiswa = request.form.get("nama_mahasiswa")
        file = request.files.get("file_proposal")

        teks = ekstrak_teks(file)
        paragraphs = [p.strip() for p in teks.split("\n") if p.strip()]
        if not teks:
            raise ValueError("Dokumen tidak mengandung teks yang dapat dianalisis")

        chunks = chunk_paragraphs(paragraphs)
        if not chunks:
            raise ValueError("Dokumen tidak memiliki paragraf valid untuk dianalisis")

        total_score = 0
        highlight_map = {}
        offset = 0  # FIX indexing

        for chunk in chunks:
            chunk_text = "\n".join(chunk)

            response = client.chat.completions.create(
                model="gpt-4o",
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": """
Analisis probabilistik teks akademik.
Kembalikan JSON:

{
 "chunk_score": 0-100,
 "paragraf_high_risk": [
   {
     "paragraf_index": number,
     "skor": number,
     "kalimat_index": [numbers],
     "alasan": "..."
   }
 ]
}
"""
                    },
                    {"role": "user", "content": chunk_text}
                ]
            )

            data = json.loads(response.choices[0].message.content)
            chunk_score = float(data.get("chunk_score", 0))
            total_score += max(0, min(100, chunk_score))

            for item in data.get("paragraf_high_risk", []):
                local_index = int(item.get("paragraf_index", -1))
                if local_index < 0 or local_index >= len(chunk):
                    continue

                global_index = offset + local_index
                if global_index < 0 or global_index >= len(paragraphs):
                    continue

                if "kalimat_index" not in item or not isinstance(item["kalimat_index"], list):
                    item["kalimat_index"] = []

                item["skor"] = max(0, min(100, int(item.get("skor", 0))))
                highlight_map[global_index] = item

            offset += len(chunk)

        skor_total = round(total_score / len(chunks), 2)
        human_prob = round(100 - skor_total, 2)

        # ===== Statistik =====
        total_paragraf = len(paragraphs)
        high = moderate = low = 0

        for i in range(total_paragraf):
            skor = highlight_map[i]["skor"] if i in highlight_map else 0
            kategori = kategori_risiko(skor)

            if kategori == "high":
                high += 1
            elif kategori == "moderate":
                moderate += 1
            else:
                low += 1

        stats = {
            "total": total_paragraf,
            "high": high,
            "moderate": moderate,
            "low": low
        }

        pdf_file = generate_pdf(nama_mahasiswa, skor_total, stats, highlight_map, paragraphs)

        # ===== SAVE TO DATABASE =====
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        sql = """
        INSERT INTO ai_checks
        (nama_penguji, nama_mahasiswa, skor_total, detail_laporan,
         file_asli, nama_pdf_laporan, ip_address, user_agent)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """

        cursor.execute(sql, (
            nama_penguji,
            nama_mahasiswa,
            skor_total,
            json.dumps(highlight_map),
            file.filename,
            str(REPORTS_DIR / pdf_file),
            request.remote_addr,
            request.headers.get("User-Agent")
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "status": "sukses",
            "skor_ai": skor_total,
            "human_prob": human_prob,
            "highlight": highlight_map,
            "paragraphs": paragraphs,
            "stats": stats,
            "pdf": f"download/{pdf_file}"
        })

    except Exception as e:
        return jsonify({"status": "error", "pesan": str(e)})

@app.route("/download/<path:filename>")
def download_file(filename):
    if not session.get("authenticated"):
        return redirect(url_for("login"))

    safe_path = (REPORTS_DIR / filename).resolve()
    if REPORTS_DIR.resolve() not in safe_path.parents:
        return jsonify({"status": "error", "pesan": "File tidak valid"}), 400
    if not safe_path.exists() or not safe_path.is_file():
        return jsonify({"status": "error", "pesan": "File tidak ditemukan"}), 404

    return send_file(safe_path, as_attachment=True)

application = app