import random
import subprocess
import csv
import logging
import re
import time
import os
import io
import requests
from pathlib import Path
from typing import Optional
from bs4 import BeautifulSoup
from PIL import Image
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc
from tqdm import tqdm
from dataclasses import dataclass, asdict, fields
from selenium.common.exceptions import WebDriverException

def get_stealth_driver(self):
    options = uc.ChromeOptions()

    if self.headless:
        options.add_argument('--headless=new')

    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    # Randomize a common User-Agent
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ]
    options.add_argument(f'--user-agent={random.choice(user_agents)}')

    driver = uc.Chrome(options=options, version_main=146)  # Match your local Chrome version
    return driver

# --- Logging & Data Structure ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("scraper_debug.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

@dataclass
class ValidatedPrimer:
    primer_bank_id: str
    protein_accession: Optional[str] = "N/A"
    gene_name: Optional[str] = "N/A"
    gene_symbol: Optional[str] = "N/A"
    forward_primer: Optional[str] = "N/A"
    reverse_primer: Optional[str] = "N/A"
    amplicon_size: Optional[int] = 0
    forward_tm: Optional[float] = None
    reverse_tm: Optional[float] = None
    full_coding_sequence: Optional[str] = "N/A"
    has_validation_data: bool = False
    validation_status: Optional[str] = "N/A"
    gel_result: Optional[str] = "N/A"
    gel_band_count: Optional[int] = None
    gel_notes: Optional[str] = "N/A"
    dimer_notes: Optional[str] = "N/A"
    qpcr_result: Optional[str] = "N/A"
    qpcr_ct: Optional[float] = None
    sequencing_result: Optional[str] = "N/A"
    blast_result: Optional[str] = "N/A"
    is_successful: Optional[bool] = None
    species: Optional[str] = "N/A"
    scrape_timestamp: Optional[str] = "N/A"
    validation_page_url: Optional[str] = "N/A"
    blast_identity: Optional[float] = None
    blast_match_length: Optional[int] = None
    blast_match_count: Optional[int] = None

class PrimerBankPipeline:
    def __init__(self, output_dir: str = "data"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.driver = None
        self.last_rotation = time.time()
        self.headless = True
        # Create a persistent session for image downloads to look "human"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://pga.mgh.harvard.edu/rtpcr/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        })

    def close_driver(self):
        """Safely shuts down the Chrome instance and clears the driver."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.error(f"Error closing driver: {e}")
            finally:
                self.driver = None

    def _init_driver(self):
        """Unified Stealth Driver using undetected-chromedriver."""
        if self.driver:
            return

        logger.info("Initializing Undetectable Chrome Instance...")
        options = uc.ChromeOptions()

        # Use the 'new' headless mode which sends proper headers
        if self.headless:
            options.add_argument('--headless=new')

        # UC handles the removal of 'cdc_' and 'webdriver' flags automatically.
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')

        # Randomize User-Agent to match your requests.Session
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ]
        chosen_ua = random.choice(user_agents)
        options.add_argument(f'--user-agent={chosen_ua}')

        # Match the session headers to the browser headers
        self.session.headers.update({"User-Agent": chosen_ua})

        try:
            self.driver = uc.Chrome(options=options, version_main=146)
            self.driver.implicitly_wait(10)
        except Exception as e:
            logger.error(f"CRITICAL: UC Driver failed to start: {e}")
            self.driver = None
            raise e

    def _wait_for_internet(self):
        """Actively monitors the connection...
        """
        time.sleep(10)

    def rotate_vpn(self):
        """Verified UI Automation for Proton VPN with full identity reset."""
        logger.info("STOPWATCH: 10-minute cooldown reached. Rotating VPN and clearing identity...")
        try:
            from pywinauto import Application

            # Clear existing browser state before closing
            if self.driver:
                try:
                    self.driver.delete_all_cookies()
                    # Local Storage/Session Storage wipe
                    self.driver.execute_script("window.localStorage.clear();")
                    self.driver.execute_script("window.sessionStorage.clear();")

                except WebDriverException:
                    pass

                self.close_driver()

                # Reset the requests.Session to kill existing TCP connections
                self.session.cookies.clear()
                self.session = requests.Session()
                # Restore WAF-bypassing headers!
                self.session.headers.update({
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Referer": "https://pga.mgh.harvard.edu/rtpcr/",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1"
                })

            # Trigger Proton VPN UI
            app = Application(backend="uia").connect(title_re=".*Proton VPN.*")
            win = app.top_window()
            win.child_window(title="Change server", control_type="Button").invoke()

            logger.info("VPN Change triggered. Purging System Caches...")

            # Flush DNS and reset IP interface to drop old sockets
            subprocess.run("ipconfig /flushdns", shell=True, check=False)

            # ACTIVE NETWORK VERIFICATION (The Fix)
            logger.info("Waiting for VPN to establish tunnel and DNS...")
            time.sleep(10)  # Give Proton 10s to drop the old connection

            timeout = 120  # Wait a maximum of 2 minutes
            start_time = time.time()

            while time.time() - start_time < timeout:
                try:
                    # Ping Harvard to verify WAF/DNS is ready
                    requests.get("https://pga.mgh.harvard.edu/", timeout=3)
                    logger.info("Network and DNS successfully verified!")
                    time.sleep(2)  # Micro-pause to let Windows networking settle
                    break
                except requests.exceptions.RequestException:
                    # Internet is still down. Wait 2 seconds and test again.
                    time.sleep(2)
            else:
                logger.error("CRITICAL: VPN failed to reconnect within 2 minutes.")

            self.last_rotation = time.time()

            # Re-init with new fingerprint
            self._init_driver()

        except Exception as e:
            logger.error(f"VPN Rotation or Cache Purge Failed: {e}")

    def check_for_ban(self, html, item):
        if "Warning: You have made so many queries recently" in html:
            logger.warning(f"IP BAN on {item}! Emergency rotation...")
            self.rotate_vpn()
            return True
        return False

    def discover_ids(self, gene, species):
        """Finds IDs from the main search page."""
        self._init_driver()
        try:
            self.driver.get("https://pga.mgh.harvard.edu/primerbank/index.html")
            WebDriverWait(self.driver, 15).until(EC.presence_of_element_located((By.NAME, "searchBox")))
            Select(self.driver.find_element(By.NAME, "selectBox")).select_by_visible_text("NCBI Gene Symbol")
            Select(self.driver.find_element(By.NAME, "species")).select_by_visible_text(species)

            box = self.driver.find_element(By.NAME, "searchBox")
            box.clear()
            box.send_keys(gene)
            self.driver.find_element(By.NAME, "Submit").click()

            WebDriverWait(self.driver, 20).until(lambda
                                                     d: "primerID=" in d.page_source or "No primer pair is found" in d.page_source or "Warning:" in d.page_source)

            html = self.driver.page_source
            if self.check_for_ban(html, gene): return self.discover_ids(gene, species)
            if "No primer pair is found" in html: return []
            return list(set(re.findall(r'primerID=(\w+)', html)))
        except Exception as e:
            logger.error(f"Discovery failed for {gene}: {e}")
            return None

    @staticmethod
    def _parse_validation_text(text: str, primer: ValidatedPrimer):
        """Parses the text of the validation page to classify success/failure modes."""
        text_lower = text.lower()

        # Gel Results
        if any(k in text_lower for k in ['single band', 'correct size', 'expected size']):
            primer.gel_result = 'single_band'
        elif any(k in text_lower for k in ['multiple band', 'two band', 'several band']):
            primer.gel_result = 'multiple_bands'
        elif any(k in text_lower for k in ['no band', 'no amplification']):
            primer.gel_result = 'no_band'
        elif any(k in text_lower for k in ['wrong size', 'incorrect size']):
            primer.gel_result = 'wrong_size'
        elif any(k in text_lower for k in ['faint', 'weak']):
            primer.gel_result = 'faint'

        # qPCR Results
        if 'amplification plot' in text_lower or 'amplification curve' in text_lower:
            primer.qpcr_result = 'data_present'
        if 'amplification detected' in text_lower or 'successful amplification' in text_lower:
            primer.qpcr_result = 'amplified'
        elif 'no amplification' in text_lower:
            primer.qpcr_result = 'no_amplification'
        elif 'poor amplification' in text_lower or 'low efficiency' in text_lower:
            primer.qpcr_result = 'poor'

        # Extract Ct Value
        ct_match = re.search(r'ct[:\s=]+(\d+\.?\d*)', text, re.IGNORECASE)
        if ct_match: primer.qpcr_ct = float(ct_match.group(1))

        # --- Extract Blast Results ---
        # Extract Percent Identity
        id_match = re.search(r'Identity[:\s]+(\d+\.?\d*)', text, re.IGNORECASE)
        if id_match:
            primer.blast_identity = float(id_match.group(1))

        # Extract Match Length
        len_match = re.search(r'Match\s*Length[:\s]+(\d+)', text, re.IGNORECASE)
        if len_match:
            primer.blast_match_length = int(len_match.group(1))

        # Extract Number of Matches
        count_match = re.search(r'of\s+(\d+)\s+Blast\s+Matches', text, re.IGNORECASE)
        if count_match:
            primer.blast_match_count = int(count_match.group(1))

        # 4. Determine BLAST Label based on combined metrics
        if primer.blast_match_count and primer.blast_match_count > 3:
            primer.blast_result = 'non_specific'  # Too many off-targets = Class 1 Failure
        elif primer.blast_identity:
            if primer.blast_identity >= 95:
                primer.blast_result = 'specific'
            elif primer.blast_identity >= 80:
                primer.blast_result = 'partial'
            else:
                primer.blast_result = 'low_identity'
                # --- Amplicon cross validation ---
                match_length = re.search(r'match\s*length[:\s]+(\d+)', text, re.IGNORECASE)
                if match_length and primer.amplicon_size:
                    length = int(match_length.group(1))
                    # If match length is close to expected amplicon size, it's a confirmed specific hit
                    if abs(length - primer.amplicon_size) < 20 and primer.blast_result != 'low_identity':
                        primer.blast_result = 'specific'

        if 'non-specific' in text_lower or 'multiple hits' in text_lower:
            primer.blast_result = 'non_specific'
        elif 'specific' in text_lower and 'non' not in text_lower and not primer.blast_result:
            primer.blast_result = 'specific'

        # Check for presence of long sequence data strings
        if len(re.findall(r'[ATCG]{50,}', text.upper())) > 0:
            primer.sequencing_result = 'data_present'

        # Success Heuristic
        pos = sum(
            [primer.gel_result == 'single_band', primer.qpcr_result == 'amplified', primer.blast_result == 'specific'])
        neg = sum(
            [primer.gel_result in ['multiple_bands', 'no_band', 'wrong_size'], primer.blast_result == 'non_specific'])

        if neg > 0:
            primer.validation_status = 'failed'
            primer.is_successful = False
        elif pos >= 2:
            primer.validation_status = 'success'
            primer.is_successful = True
        else:
            primer.validation_status = 'unknown'
            primer.is_successful = None

    def scrape_details(self, pid, retry_count=0):
        """High-precision extraction with hybrid URL handling, fallbacks, and smart skipping."""
        clean_pid = re.sub(r'\D+$', '', str(pid).strip())

        # Initialise immediately for safe file loading
        p = ValidatedPrimer(primer_bank_id=clean_pid)
        p.scrape_timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        # Loop Breaker
        if retry_count >= 3:
            logger.error(f"FATAL: ID {clean_pid} failed 3 consecutive times. Logging failure to CSV.")
            p.validation_status = "error_retry_limit"
            return p

        self._init_driver()
        try:
            # Meta Data Page
            detail_url = f"https://pga.mgh.harvard.edu/cgi-bin/primerbank/new_displayDetail2.cgi?primerID={clean_pid}"

            self.driver.get(detail_url)
            html_detail = self.driver.page_source

            if self.check_for_ban(html_detail, clean_pid):
                return self.scrape_details(clean_pid, retry_count=retry_count + 1)

            # ID detector
            if "Cannot find primer pair" in html_detail or "Error:" in html_detail:
                logger.warning(f"ID {clean_pid} is a dead link (Not in database). Logging to CSV.")
                p.validation_status = "error_dead_link"
                return p
            # -----------------------------

            soup = BeautifulSoup(html_detail, 'html.parser')

            # Metadata extraction
            text_clean = soup.get_text(" ", strip=True)
            text_nospaces = soup.get_text("", strip=True).upper()

            # Table structure
            for table in soup.find_all('table'):
                for row in table.find_all('tr'):
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        label = cells[0].get_text(" ", strip=True).lower()

                        # Fwd primer Tm
                        if 'forward primer' in label or 'left primer' in label:
                            seq = re.search(r'([ACGTacgt]{15,40})', cells[1].get_text(" ", strip=True))
                            if seq and p.forward_primer == "N/A": p.forward_primer = seq.group(1).upper()

                            # Tm is in the 4th column (index 3)
                            if len(cells) >= 4:
                                tm_match = re.search(r'(\d{2}\.\d+)', cells[3].get_text(" ", strip=True))
                                if tm_match and not p.forward_tm: p.forward_tm = float(tm_match.group(1))

                        # Rev Primer and Tm
                        elif 'reverse primer' in label or 'right primer' in label:
                            seq = re.search(r'([ACGTacgt]{15,40})', cells[1].get_text(" ", strip=True))
                            if seq and p.reverse_primer == "N/A": p.reverse_primer = seq.group(1).upper()

                            # Tm is in the 4th column (index 3)
                            if len(cells) >= 4:
                                tm_match = re.search(r'(\d{2}\.\d+)', cells[3].get_text(" ", strip=True))
                                if tm_match and not p.reverse_tm: p.reverse_tm = float(tm_match.group(1))

                        # Amplicon size
                        elif 'amplicon size' in label or 'product size' in label:
                            sz = re.search(r'(\d+)', cells[1].get_text(" ", strip=True))
                            if sz and p.amplicon_size == 0: p.amplicon_size = int(sz.group(1))

            # REGEX fallbacks if soup fails
            if p.forward_primer == "N/A":
                m = re.search(r'(?:Forward|Left).*?([ACGTacgt]{15,40})', text_clean, re.IGNORECASE)
                if m: p.forward_primer = m.group(1).upper()

            if p.reverse_primer == "N/A":
                m = re.search(r'(?:Reverse|Right).*?([ACGTacgt]{15,40})', text_clean, re.IGNORECASE)
                if m: p.reverse_primer = m.group(1).upper()

            # Gene info for extraction
            symbol_match = re.search(r'GenBank Accession\s*([A-Za-z0-9_]+)', text_clean, re.IGNORECASE)
            if symbol_match:
                p.gene_symbol = symbol_match.group(1).strip()
            else:
                id_match = re.search(r'NCBI GeneID\s*(\d+)', text_clean, re.IGNORECASE)
                if id_match: p.gene_symbol = id_match.group(1).strip()

            gene_match = re.search(r'Gene Description\s*(.*?)(?=Primer Pair|PrimerBank|$)', text_clean, re.IGNORECASE)
            if gene_match: p.gene_name = gene_match.group(1).strip()

            acc_match = re.search(r'Protein Accession\s*([A-Za-z0-9_]+)', text_clean, re.IGNORECASE)
            if acc_match: p.protein_accession = acc_match.group(1).strip()

            # Full coding sequence extraction
            seq_span = soup.find('span', class_='sequence')
            if seq_span:
                p.full_coding_sequence = re.sub(r'[^ACGTacgt]', '', seq_span.get_text()).upper()
            else:
                # Fallback: Slice the raw text after the header
                full_text = soup.get_text()
                if "Location in Coding Sequence" in full_text:
                    # Split the text at the exact end of the header to bypass English letters
                    parts = full_text.split("highlighted)")
                    if len(parts) > 1:
                        raw_chunk = parts[-1]
                    else:
                        # Backup split if "highlighted)" is missing
                        raw_chunk = full_text.split("Location in Coding Sequence")[-1]

                    p.full_coding_sequence = re.sub(r'[^ACGTacgt]', '', raw_chunk).upper()

            # Species Extraction
            text_lower = text_clean.lower()
            if 'mus musculus' in text_lower or 'mouse' in text_lower:
                p.species = 'Mouse'
            elif 'homo sapiens' in text_lower or 'human' in text_lower:
                p.species = 'Human'
            # -----------------------------------

            # Validation Page Extraction
            validation_link_present = soup.find('a', href=re.compile(r'displayResult\.do|rtpcr|displayValidation',
                                                                     re.IGNORECASE))
            if validation_link_present:
                time.sleep(random.uniform(2, 3))

                # Attempt 1: Fast Hardcoded Modern URL
                valid_url = f"https://pga.mgh.harvard.edu/rtpcr/displayResult.do?v=2&primerPairId={clean_pid}"
                self.driver.get(valid_url)
                valid_html = self.driver.page_source

                # Check if Attempt 1 failed (Server returns 404, Error, or Not Found)
                if any(err in valid_html for err in ["404 Not Found", "Error:", "Cannot find"]):
                    logger.warning(f"Hardcoded URL failed for {clean_pid}. Attempting dynamic fallback...")

                    # Attempt 2: Dynamic link extraction from the original soup
                    valid_link = soup.find('a',
                                           href=re.compile(r'displayResult\.do|rtpcr|displayValidation', re.IGNORECASE))
                    if valid_link:
                        href = valid_link['href']
                        valid_url = href if href.startswith(
                            'http') else f"https://pga.mgh.harvard.edu/{href.lstrip('/')}"
                        self.driver.get(valid_url)
                        valid_html = self.driver.page_source
                    else:
                        raise Exception("Validation link not found in dynamic fallback.")

                p.validation_page_url = valid_url
                p.has_validation_data = True
                valid_soup = BeautifulSoup(valid_html, 'html.parser')
                self._parse_validation_text(valid_soup.get_text(), p)

                # AUTOMATIC FOLDER ROUTING
                img_base_dir = self.output_dir / "images"

                # Define specific subdirectories
                dirs = {
                    "amp": img_base_dir / "amp_plots",
                    "diss": img_base_dir / "dissociation_curves",
                    "gel_sample": img_base_dir / "gels_sample",
                    "gel_ladder": img_base_dir / "gels_ladder"
                }

                # Create all folders if they don't exist
                for d in dirs.values():
                    d.mkdir(parents=True, exist_ok=True)

                gel_count = 0

                for img in self.driver.find_elements(By.TAG_NAME, "img"):
                    src = img.get_attribute("src")
                    if not src: continue

                    # Route Amplification Plots
                    if "ampPlot" in src:
                        time.sleep(random.uniform(0.5, 1.5))
                        self._save_image(src, dirs["amp"] / f"{clean_pid}_amp.jpg")

                    # Route Dissociation Curves
                    elif "dissCurve" in src:
                        time.sleep(random.uniform(0.5, 1.5))
                        self._save_image(src, dirs["diss"] / f"{clean_pid}_diss.jpg")

                    # Route Gels (Top-to-Bottom DOM parsing = Left-to-Right visual parsing)
                    elif "gelImage" in src:
                        gel_count += 1
                        time.sleep(random.uniform(0.5, 1.5))
                        if gel_count == 1:
                            # First gel found = Left Image (Sample)
                            self._save_image(src, dirs["gel_sample"] / f"{clean_pid}_gel_sample.jpg")
                        elif gel_count == 2:
                            # Second gel found = Right Image (Ladder)
                            self._save_image(src, dirs["gel_ladder"] / f"{clean_pid}_gel_ladder.jpg")

            return p

        # THE ERROR CATCHER
        except Exception as e:
            error_msg = str(e)

            # Check if it's a physical network/WiFi dropout or DNS failure
            network_errors = ["ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_CONNECTION", "WinError 10054"]

            if any(err in error_msg for err in network_errors):
                logger.error(f"Network issue detected on {clean_pid}.")

                # --- VPN Rotation Trigger ---
                # ERR_NAME_NOT_RESOLVED automatically skips dead VPN nodes
                if any(err in error_msg for err in ["10054", "ERR_CONNECTION_CLOSED", "ERR_NAME_NOT_RESOLVED"]):
                    logger.warning("DNS failure or IP Block detected (Bad VPN Node). Rotating VPN...")
                    self.rotate_vpn()
                else:
                    # Otherwise, it's a standard Wi-Fi drop (like ERR_INTERNET_DISCONNECTED)
                    logger.info("Waiting 30 seconds for Windows to auto-reconnect...")
                    time.sleep(30)
                # -------------------------------------

                logger.info(f"Re-attempting failed ID: {clean_pid}")
                self._init_driver()  # Ensure browser is still alive

                # Make sure we pass the retry_count so we don't infinite loop!
                return self.scrape_details(clean_pid, retry_count=retry_count + 1)

                # If it's a normal HTML/Parsing error, log it and save the partial data
            logger.error(f"Scrape failed for {clean_pid}: {e}. Saving partial data to CSV.")

            # Tag it to find it in Excel later
            if p.validation_status == "N/A":
                p.validation_status = "error_parsing"

            return p

    def _save_image(self, url, path):
        """Safely download images using the driver's current authenticated cookies."""
        try:
            # Transfer Selenium's identity to the requests module
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://pga.mgh.harvard.edu/"
            }
            cookies = {c['name']: c['value'] for c in self.driver.get_cookies()}

            response = requests.get(url, headers=headers, cookies=cookies, timeout=15)

            # --- FAIL-SAFE LOGIC ---
            if response.status_code == 403:
                logger.warning(f"Image blocked by Firewall (403): {url}. Skipping image to preserve text data.")
                return False  # Gracefully fail so the script doesn't crash

            response.raise_for_status()

            image = Image.open(io.BytesIO(response.content))
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")

            image.save(path, "JPEG", quality=85)
            return True

        except Exception as e:
            logger.error(f"Failed to save image {url}: {e}")
            return False

# UI INTERFACE

def main_menu():
    pipeline = PrimerBankPipeline()
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        print("====================================================")
        print("   PRIMERBANK RPA PIPELINE - DATA ENGINEERING V2.2  ")
        print("====================================================")
        print(f" STATUS: Headless [{'ON' if pipeline.headless else 'OFF'}] | Dir: {os.getcwd()}")
        print("----------------------------------------------------")
        print("1. DISCOVER: Find Primer IDs from a Gene List")
        print("2. SCRAPE:   Extract Data/Images from ID List")
        print("3. TOGGLE:   Switch Headless Mode")
        print("4. EXIT")
        print("----------------------------------------------------")

        choice = input("Select [1-4]: ").strip()

        if choice == '1':
            species = input("Species (Human/Mouse): ").strip().capitalize()
            input_file = input("Path to Gene list: ").strip()
            if not os.path.exists(input_file): continue

            with open(input_file) as f:
                genes = [l.strip() for l in f if l.strip()]
            id_path = pipeline.output_dir / f"ids_{species.lower()}.txt"
            log_path = pipeline.output_dir / f"done_{species.lower()}.txt"

            done = set()
            if log_path.exists():
                with open(log_path) as f: done = set(l.strip() for l in f)

            pending = [g for g in genes if g not in done]
            with open(id_path, 'a') as f_id, open(log_path, 'a') as f_log:
                for gene in tqdm(pending, desc="Discovering"):
                    if time.time() - pipeline.last_rotation >= 610: pipeline.rotate_vpn()
                    ids = pipeline.discover_ids(gene, species)
                    if ids is not None:
                        for pid in ids: f_id.write(f"{pid}\n")
                        f_log.write(f"{gene}\n")
                        f_id.flush()
                        f_log.flush()
                        time.sleep(random.uniform(3.5, 5.0))

        elif choice == '2':
            input_file = input("Path to ID list: ").strip()
            if not os.path.exists(input_file): continue

            # Explicitly define and print the output path
            out_csv = pipeline.output_dir / "validated_primers.csv"
            print(f"\n[INFO] Saving data to: {out_csv.absolute()}")
            with open(input_file) as f:

                # Sanitize the input list immediately
                all_ids = list(set(re.sub(r'\D+$', '', l.strip()) for l in f if l.strip()))

            done_ids = set()
            if out_csv.exists():
                with open(out_csv, 'r', encoding='utf-8') as f:
                    # Sanitize the CSV list so the two sets perfectly match

                    done_ids = set(re.sub(r'\D+$', '', r['primer_bank_id']) for r in csv.DictReader(f))
            pending = [pid for pid in all_ids if pid not in done_ids]
            print(f"[INFO] Found {len(done_ids)} completed IDs. {len(pending)} remaining to scrape.\n")

            headers = [f.name for f in fields(ValidatedPrimer)]
            file_exists = out_csv.exists()
            with open(out_csv, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)

                if not file_exists:
                    writer.writeheader()

                for pid in tqdm(pending, desc="Scraping"):

                    # VPN Rotation check
                    if time.time() - pipeline.last_rotation >= 610:
                        pipeline.rotate_vpn()
                    data = pipeline.scrape_details(pid)
                    if data:
                        writer.writerow(asdict(data))
                        f.flush()

                    # Loop speed: 4 to 6 seconds between primers
                    sleep_time = random.uniform(4.0, 6.0)
                    time.sleep(sleep_time)

        elif choice == '3':

            pipeline.headless = not pipeline.headless
            pipeline.close_driver()  # <--- Removed underscore
            print(f"\n[SUCCESS] Headless mode is now {'ON' if pipeline.headless else 'OFF'}")
            input("Press Enter to return to the main menu...")

        elif choice == '4':
            print("\n[INFO] Shutting down pipeline and closing browsers...")
            pipeline.close_driver()  # <--- Removed underscore
            break

if __name__ == "__main__":
    main_menu()