import json
import re
import shutil
import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, unquote

import requests
import streamlit as st
from bs4 import BeautifulSoup
from pypdf import PdfReader
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- Configuration ---
BASE_URL = os.getenv("BASE_URL", "https://www.justice.gov/epstein/court-records")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
INDEX_FILE = Path(os.getenv("INDEX_FILE", "search_corpus.json"))
MAX_WORKERS_DOWNLOADS = int(os.getenv("MAX_WORKERS_DOWNLOADS", "8"))
MAX_WORKERS_PARSING = int(os.getenv("MAX_WORKERS_PARSING", "4"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Setup logging
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# --- Mobile-Friendly CSS ---
MOBILE_CSS = """
<style>
    /* 1. Global Background & Mobile Optimization */
    .stApp {
        background-color: #40407a;
        color: #f7f1e3;
    }
    
    /* Mobile-specific adjustments */
    @media (max-width: 768px) {
        .stApp {
            padding: 0.5rem;
        }
        .stColumns {
            gap: 0.5rem;
        }
    }
    
    /* 2. Headers & Text */
    h1, h2, h3, .stMarkdown, .stText, p {
        color: #f7f1e3 !important;
        font-family: 'Helvetica Neue', sans-serif;
    }
    
    /* 3. Neumorphic Cards (Mobile-Optimized) */
    .streamlit-expanderHeader, .stAlert {
        background-color: #40407a !important;
        border-radius: 15px !important;
        box-shadow: 5px 5px 10px rgba(0, 0, 0, 0.3), -5px -5px 10px rgba(255, 255, 255, 0.05);
        border: none !important;
        color: #f7f1e3 !important;
        margin-bottom: 0.5rem;
        padding: 0.5rem;
    }
    
    /* 4. Neumorphic Buttons (Mobile-Optimized) */
    div.stButton > button {
        background-color: #40407a;
        color: #f7f1e3;
        border: none;
        border-radius: 25px;
        box-shadow: 3px 3px 6px 0 rgba(0, 0, 0, 0.3),
                    -3px -3px 6px 0 rgba(255, 255, 255, 0.05);
        transition: all 0.2s ease-in-out;
        padding: 0.4rem 1rem;
        font-weight: 600;
        width: 100%;
        margin-bottom: 0.5rem;
    }
    
    /* Button Hover & Active States */
    div.stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 6px 6px 12px 0 rgba(0, 0, 0, 0.3),
                    -6px -6px 12px 0 rgba(255, 255, 255, 0.05);
        color: #33d9b2;
    }
    
    div.stButton > button:active {
        box-shadow: inset 2px 2px 4px 0 rgba(0, 0, 0, 0.3),
                    inset -2px -2px 4px 0 rgba(255, 255, 255, 0.05);
        transform: translateY(0px);
    }

    /* 5. Inputs (Mobile-Optimized) */
    div.stTextInput > div > div > input {
        background-color: #40407a;
        border-radius: 12px;
        border: none;
        box-shadow: inset 3px 3px 6px 0 rgba(0, 0, 0, 0.3),
                    inset -3px -3px 6px 0 rgba(255, 255, 255, 0.05);
        color: #f7f1e3;
        padding-left: 12px;
        font-size: 16px; /* Prevents zoom on iOS */
    }

    div.stTextInput > label {
        color: #f7f1e3 !important;
    }
    
    /* 6. Mobile-Friendly Progress Bar */
    .stProgress > div > div > div > div {
        background-color: #34ace0;
    }
    
    /* 7. Responsive Expander Content */
    .streamlit-expanderContent {
        padding: 0.5rem;
        color: #f7f1e3;
    }
</style>
"""

# --- Helper Functions (Robust Logic) ---

def initialize_session_state() -> None:
    """Initialize session state variables"""
    if "pdf_links" not in st.session_state:
        st.session_state.pdf_links = []
    if "is_downloaded" not in st.session_state:
        st.session_state.is_downloaded = False
    if "corpus_built" not in st.session_state:
        st.session_state.corpus_built = INDEX_FILE.exists()
    if "search_history" not in st.session_state:
        st.session_state.search_history = []

def validate_search_term(term: str) -> bool:
    """Validate search term to prevent injection attacks"""
    if not term or len(term.strip()) == 0:
        return False
    # Add more validation as needed
    return True

def get_pdf_links_robust(url: str) -> List[str]:
    """Scrapes links using BeautifulSoup with error handling"""
    try:
        logger.info(f"Fetching PDF links from {url}")
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        links = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href'].strip()
            if href.lower().endswith('.pdf'):
                full_url = urljoin(url, href)
                links.add(full_url)
        logger.info(f"Found {len(links)} PDF links")
        return sorted(list(links))
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching links: {e}")
        st.error(f"Error fetching links: {e}")
        return []

def download_pdf_headless(url: str) -> Optional[Path]:
    """Downloads files with improved error handling"""
    try:
        logger.info(f"Downloading {url}")
        parsed_url = urlparse(url)
        path_parts = unquote(parsed_url.path).split('/')
        try:
            idx = path_parts.index("Court Records")
            relative_path = Path(*path_parts[idx + 1 :])
        except ValueError:
            relative_path = Path(path_parts[-1])

        local_path = DOWNLOAD_DIR / relative_path
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if local_path.exists():
            logger.info(f"File already exists: {local_path}")
            return local_path

        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Successfully downloaded to {local_path}")
        return local_path
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading {url}: {e}")
        return None

def extract_text_from_pdf(file_path: Path) -> Dict[str, any]:
    """Extracts text for indexing with error handling"""
    page_data = {}
    try:
        logger.info(f"Extracting text from {file_path}")
        reader = PdfReader(file_path)
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                page_data[i + 1] = text
    except Exception as e:
        logger.error(f"Error extracting text from {file_path}: {e}")
    return {"file": str(file_path.relative_to(DOWNLOAD_DIR)), "content": page_data}

def build_search_corpus(progress_bar, status_text) -> None:
    """Builds the search index with parallel processing"""
    files = list(DOWNLOAD_DIR.rglob("*.pdf"))
    total_files = len(files)
    corpus = {}

    if total_files == 0:
        status_text.text("No PDF files found to index.")
        return

    logger.info(f"Building search corpus from {total_files} files")
    status_text.text("Parsing PDFs for text...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_PARSING) as executor:
        future_to_file = {executor.submit(extract_text_from_pdf, f): f for f in files}
        completed = 0
        for future in as_completed(future_to_file):
            result = future.result()
            if result["content"]:
                corpus[result["file"]] = result["content"]
            completed += 1
            progress_bar.progress(completed / total_files)

    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(corpus, f)
        st.session_state.corpus_built = True
        status_text.text("Index Built Successfully.")
        logger.info("Search corpus built successfully")
    except Exception as e:
        logger.error(f"Error saving search corpus: {e}")
        status_text.text("Error building search index.")

def search_corpus(term: str) -> List[Dict[str, any]]:
    """Search the corpus with input validation"""
    if not validate_search_term(term):
        return []
    
    if not INDEX_FILE.exists():
        logger.warning("Search index file not found")
        return []
    
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            corpus = json.load(f)
    except Exception as e:
        logger.error(f"Error loading search corpus: {e}")
        return []
    
    results = []
    term = term.lower()
    logger.info(f"Searching for term: {term}")
    
    for filename, pages in corpus.items():
        matched_pages = []
        for page_num, text in pages.items():
            if term in text.lower():
                matched_pages.append(page_num)
        if matched_pages:
            results.append({"file": filename, "pages": matched_pages})
    
    logger.info(f"Found {len(results)} matching files")
    return results

# --- Main App ---

def main():
    st.set_page_config(
        page_title="DOJ Archive", 
        layout="centered",  # Changed to centered for better mobile experience
        page_icon="⚖️",
        initial_sidebar_state="collapsed"  # Start with sidebar collapsed on mobile
    )
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    
    initialize_session_state()

    st.title("DOJ Court Archive")
    st.caption("Mobile-Optimized / Parallel Ingestion / Search Index")
    st.markdown("---")

    # Mobile-friendly tabs instead of columns
    tab1, tab2 = st.tabs(["Ingest Data", "Search Data"])

    with tab1:
        st.subheader("Ingest Data")
        if st.button("1. Scan for Links", key="scan_links"):
            with st.spinner("Scanning..."):
                st.session_state.pdf_links = get_pdf_links_robust(BASE_URL)
            if st.session_state.pdf_links:
                st.success(f"Found {len(st.session_state.pdf_links)} Links")

        if st.session_state.pdf_links:
            st.write(f"Queue: {len(st.session_state.pdf_links)} items")
            if st.button("2. Download & Index", key="download_index"):
                progress = st.progress(0)
                status = st.empty()
                
                if not DOWNLOAD_DIR.exists():
                    DOWNLOAD_DIR.mkdir(parents=True)
                
                links = st.session_state.pdf_links
                status.text(f"Downloading {len(links)} files...")
                
                with ThreadPoolExecutor(max_workers=MAX_WORKERS_DOWNLOADS) as executor:
                    futures = [executor.submit(download_pdf_headless, url) for url in links]
                    for i, _ in enumerate(as_completed(futures)):
                        progress.progress((i + 1) / len(links))
                        status.text(f"Downloading {i+1}/{len(links)}")
                
                build_search_corpus(progress, status)
                st.balloons()
                st.success("Complete.")

    with tab2:
        st.subheader("Search Data")
        
        # Search history
        if st.session_state.search_history:
            with st.expander("Recent Searches"):
                for term in st.session_state.search_history[-5:]:
                    if st.button(term, key=f"history_{term}"):
                        st.session_state.current_search = term
        
        search_term = st.text_input("Keyword", placeholder="e.g. Maxwell", 
                                    value=st.session_state.get("current_search", ""))
        
        if st.button("Search", key="search_button", disabled=not st.session_state.corpus_built):
            if not search_term:
                st.warning("Enter a term.")
            else:
                # Add to search history
                if search_term not in st.session_state.search_history:
                    st.session_state.search_history.append(search_term)
                
                matches = search_corpus(search_term)
                if matches:
                    st.info(f"Matches in {len(matches)} files.")
                    for m in matches:
                        with st.expander(f"📄 {m['file']}"):
                            st.write(f"Pages: {m['pages']}")
                else:
                    st.warning("No matches found.")

if __name__ == "__main__":
    main()

