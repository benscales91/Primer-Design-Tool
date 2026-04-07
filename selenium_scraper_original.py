#!/usr/bin/env python3
"""
PrimerBank Selenium Scraper

Full scraper using Selenium to:
1. Navigate PrimerBank pages (handles JavaScript)
2. Follow validation result links
3. Extract success/failure data from validation pages
4. Enumerate primer IDs systematically

PrimerBank ID Format:
- <protein_id>a<1-3> for version 'a' primers
- <protein_id>b<1-3> for version 'b' primers
- <protein_id>c<1-3> for version 'c' primers

Example: 6679201a1 = protein ID 6679201, primer pair a1
"""

import csv
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# PrimerBank URLs
BASE_URL = "https://pga.mgh.harvard.edu"
DETAIL_URL = f"{BASE_URL}/cgi-bin/primerbank/new_displayDetail2.cgi"
VALIDATION_URL = f"{BASE_URL}/cgi-bin/primerbank/displayValidation.cgi"


@dataclass
class ValidatedPrimer:
    """Data structure for a validated primer pair"""
    primer_bank_id: str
    protein_accession: Optional[str] = None
    gene_name: Optional[str] = None
    gene_symbol: Optional[str] = None

    # Primer sequences
    forward_primer: Optional[str] = None
    reverse_primer: Optional[str] = None

    # Amplicon info
    amplicon_size: Optional[int] = None

    # Primer properties
    forward_tm: Optional[float] = None
    reverse_tm: Optional[float] = None

    # VALIDATION RESULTS - The key data we need
    has_validation_data: bool = False
    validation_status: Optional[str] = None  # 'success', 'failed', 'not_tested'

    # Gel electrophoresis
    gel_result: Optional[str] = None  # 'single_band', 'multiple_bands', 'no_band', 'wrong_size', 'faint'
    gel_band_count: Optional[int] = None
    gel_notes: Optional[str] = None

    # qPCR results
    qpcr_result: Optional[str] = None  # 'amplified', 'no_amplification', 'poor'
    qpcr_ct: Optional[float] = None

    # Sequencing/BLAST
    sequencing_result: Optional[str] = None  # 'verified', 'mismatch', 'no_data'
    blast_result: Optional[str] = None  # 'specific', 'non_specific'

    # Binary success for ML
    is_successful: Optional[bool] = None

    # Metadata
    species: Optional[str] = None
    scrape_timestamp: Optional[str] = None
    validation_page_url: Optional[str] = None


class SeleniumPrimerBankScraper:
    """Selenium-based scraper for PrimerBank with validation data extraction"""

    def __init__(self, output_dir: str = "data", headless: bool = True, delay: float = 1.0):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.headless = headless
        self.delay = delay
        self.driver = None

    def _init_driver(self):
        """Initialize Chrome WebDriver"""
        if self.driver is not None:
            return

        options = Options()
        if self.headless:
            options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.implicitly_wait(10)
            logger.info("Chrome WebDriver initialized")
        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def _close_driver(self):
        """Close WebDriver"""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def fetch_primer_detail(self, primer_id: str) -> Optional[ValidatedPrimer]:
        """
        Fetch primer detail page and extract basic info + validation link
        """
        self._init_driver()

        url = f"{DETAIL_URL}?primerID={primer_id}"
        primer = ValidatedPrimer(primer_bank_id=primer_id)
        primer.scrape_timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        try:
            self.driver.get(url)
            time.sleep(self.delay)

            html = self.driver.page_source
            soup = BeautifulSoup(html, 'lxml')

            text = soup.get_text()

            # Check if primer exists
            if "No primer" in text or "not found" in text.lower() or "error" in text.lower():
                logger.debug(f"Primer {primer_id} not found")
                return None

            # Extract basic info from page
            self._parse_detail_page(soup, primer)

            # Find and follow validation link
            validation_link = self._find_validation_link(soup)
            if validation_link:
                primer.validation_page_url = validation_link
                self._fetch_validation_data(validation_link, primer)
            else:
                primer.has_validation_data = False
                primer.validation_status = 'not_tested'

            return primer

        except Exception as e:
            logger.error(f"Error fetching primer {primer_id}: {e}")
            return None

    def _parse_detail_page(self, soup: BeautifulSoup, primer: ValidatedPrimer):
        """Parse the primer detail page for basic info"""
        text = soup.get_text()

        # Extract gene symbol
        gene_match = re.search(r'Gene\s*(?:Symbol)?[:\s]+([A-Za-z0-9_-]+)', text, re.IGNORECASE)
        if gene_match:
            primer.gene_symbol = gene_match.group(1)

        # Extract protein accession
        prot_match = re.search(r'(NP_\d+|[A-Z]{3}\d{5})', text)
        if prot_match:
            primer.protein_accession = prot_match.group(1)

        # Extract primer sequences from tables or text
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    label = cells[0].get_text().strip().lower()
                    value = cells[1].get_text().strip()

                    if 'forward' in label or 'left' in label or "5'" in label:
                        seq = re.search(r'([ATCG]{15,40})', value.upper())
                        if seq:
                            primer.forward_primer = seq.group(1)
                    elif 'reverse' in label or 'right' in label or "3'" in label:
                        seq = re.search(r'([ATCG]{15,40})', value.upper())
                        if seq:
                            primer.reverse_primer = seq.group(1)
                    elif 'size' in label or 'length' in label or 'amplicon' in label:
                        size = re.search(r'(\d+)', value)
                        if size:
                            primer.amplicon_size = int(size.group(1))
                    elif 'tm' in label:
                        tm = re.search(r'(\d+\.?\d*)', value)
                        if tm:
                            if primer.forward_tm is None:
                                primer.forward_tm = float(tm.group(1))
                            else:
                                primer.reverse_tm = float(tm.group(1))

        # Fallback: extract from text using patterns
        if not primer.forward_primer:
            # Pattern: Forward primer: SEQUENCE or 5'-SEQUENCE-3'
            fwd_patterns = [
                r"[Ff]orward[:\s]+(?:5'-?)?([ATCG]{15,40})",
                r"[Ll]eft[:\s]+(?:5'-?)?([ATCG]{15,40})",
                r"5'[:\s-]+([ATCG]{15,40})",
            ]
            for pattern in fwd_patterns:
                match = re.search(pattern, text)
                if match:
                    primer.forward_primer = match.group(1)
                    break

        if not primer.reverse_primer:
            rev_patterns = [
                r"[Rr]everse[:\s]+(?:5'-?)?([ATCG]{15,40})",
                r"[Rr]ight[:\s]+(?:5'-?)?([ATCG]{15,40})",
                r"3'[:\s-]+([ATCG]{15,40})",
            ]
            for pattern in rev_patterns:
                match = re.search(pattern, text)
                if match:
                    primer.reverse_primer = match.group(1)
                    break

        # Amplicon size fallback
        if not primer.amplicon_size:
            size_match = re.search(r'(\d+)\s*(?:bp|bases?)', text, re.IGNORECASE)
            if size_match:
                primer.amplicon_size = int(size_match.group(1))

        # Species detection
        if 'mouse' in text.lower() or 'mus musculus' in text.lower():
            primer.species = 'mouse'
        elif 'human' in text.lower() or 'homo sapiens' in text.lower():
            primer.species = 'human'

    def _find_validation_link(self, soup: BeautifulSoup) -> Optional[str]:
        """Find the link to validation results page"""
        # Look for links containing 'validation', 'experimental', 'results'
        for link in soup.find_all('a', href=True):
            href = link['href']
            link_text = link.get_text().lower()

            # Check for known validation URL patterns
            if 'displayResult.do' in href or 'rtpcr' in href:
                if href.startswith('http'):
                    return href
                elif href.startswith('/'):
                    return "http://pga.mgh.harvard.edu" + href
                else:
                    return "http://pga.mgh.harvard.edu/" + href

            if any(keyword in link_text for keyword in ['validation', 'experimental', 'qpcr', 'results']):
                if href.startswith('/'):
                    return BASE_URL + href
                elif href.startswith('http'):
                    return href
                else:
                    return BASE_URL + '/' + href

            # Also check href itself
            if 'validation' in href.lower() or 'displayValidation' in href:
                if href.startswith('/'):
                    return BASE_URL + href
                elif href.startswith('http'):
                    return href
                else:
                    return BASE_URL + '/' + href

        return None

    def _fetch_validation_data(self, validation_url: str, primer: ValidatedPrimer):
        """
        Navigate to validation page and extract success/failure data

        The validation page contains:
        - qPCR amplification plot (image)
        - Dissociation curve (image)
        - Gel image (shows bands - single band = pass, multiple = fail)
        - Sequence data
        - BLAST results (specific = pass, multiple hits = fail)
        """
        try:
            self.driver.get(validation_url)
            time.sleep(self.delay)

            html = self.driver.page_source
            soup = BeautifulSoup(html, 'lxml')
            text = soup.get_text()
            text_lower = text.lower()

            primer.has_validation_data = True

            # Parse gel electrophoresis results
            self._parse_gel_results(text, primer)

            # Parse qPCR results
            self._parse_qpcr_results(text, primer)

            # Parse sequencing/BLAST results
            self._parse_sequencing_results(text, primer)

            # Determine overall success/failure
            self._determine_success(primer)

        except Exception as e:
            logger.error(f"Error fetching validation data: {e}")
            primer.has_validation_data = False
            primer.validation_status = 'error'

    def _parse_gel_results(self, text: str, primer: ValidatedPrimer):
        """Parse gel electrophoresis results from validation page"""
        text_lower = text.lower()

        # Look for gel-related keywords
        if 'single band' in text_lower or 'correct size' in text_lower or 'expected size' in text_lower:
            primer.gel_result = 'single_band'
        elif 'multiple band' in text_lower or 'two band' in text_lower or 'several band' in text_lower:
            primer.gel_result = 'multiple_bands'
        elif 'no band' in text_lower or 'no amplification' in text_lower:
            primer.gel_result = 'no_band'
        elif 'wrong size' in text_lower or 'incorrect size' in text_lower:
            primer.gel_result = 'wrong_size'
        elif 'faint' in text_lower or 'weak' in text_lower:
            primer.gel_result = 'faint'
        elif 'agarose gel' in text_lower or 'gel image' in text_lower:
            # Has gel data but need to infer from other clues
            primer.gel_result = 'present'

    def _parse_qpcr_results(self, text: str, primer: ValidatedPrimer):
        """Parse qPCR/SYBR Green results"""
        text_lower = text.lower()

        # Check if qPCR data is present (amplification plot shown)
        if 'amplification plot' in text_lower or 'amplification curve' in text_lower:
            primer.qpcr_result = 'data_present'

        # Look for amplification keywords
        if 'amplification detected' in text_lower or 'successful amplification' in text_lower:
            primer.qpcr_result = 'amplified'
        elif 'no amplification' in text_lower:
            primer.qpcr_result = 'no_amplification'
        elif 'poor amplification' in text_lower or 'low efficiency' in text_lower:
            primer.qpcr_result = 'poor'

        # Try to extract Ct value
        ct_match = re.search(r'ct[:\s=]+(\d+\.?\d*)', text, re.IGNORECASE)
        if ct_match:
            primer.qpcr_ct = float(ct_match.group(1))

    def _parse_sequencing_results(self, text: str, primer: ValidatedPrimer):
        """Parse sequencing and BLAST results from validation page"""
        text_lower = text.lower()

        # Look for sequence data presence
        if len(re.findall(r'[ATCG]{50,}', text)) > 0:
            primer.sequencing_result = 'data_present'

        # Parse BLAST results
        # Look for match information
        identity_match = re.search(r'(?:percent\s*)?identity[:\s]+(\d+\.?\d*)', text, re.IGNORECASE)
        if identity_match:
            identity = float(identity_match.group(1))
            if identity >= 95:
                primer.blast_result = 'specific'
            elif identity >= 80:
                primer.blast_result = 'partial'
            else:
                primer.blast_result = 'low_identity'

        match_length = re.search(r'match\s*length[:\s]+(\d+)', text, re.IGNORECASE)
        if match_length:
            length = int(match_length.group(1))
            # If match length is close to expected amplicon size, it's good
            if primer.amplicon_size and abs(length - primer.amplicon_size) < 20:
                if primer.blast_result != 'low_identity':
                    primer.blast_result = 'specific'

        # Check for multiple BLAST hits (indicates non-specificity)
        blast_hits = len(re.findall(r'gi\|\d+\|', text))
        if blast_hits > 3:
            primer.blast_result = 'non_specific'
        elif blast_hits == 1 and primer.blast_result is None:
            primer.blast_result = 'specific'

        # Explicit keywords
        if 'non-specific' in text_lower or 'multiple hits' in text_lower:
            primer.blast_result = 'non_specific'
        elif 'specific' in text_lower and 'non' not in text_lower:
            if primer.blast_result is None:
                primer.blast_result = 'specific'

    def _determine_success(self, primer: ValidatedPrimer):
        """
        Determine overall success/failure based on all validation results

        Based on PrimerBank paper criteria:
        - Success requires: single band on gel + BLAST specificity + (sequencing match if available)
        - Failure: multiple bands, no amplification, non-specific BLAST
        """
        # Count positive and negative indicators
        positive = 0
        negative = 0

        # Gel results
        if primer.gel_result == 'single_band':
            positive += 2  # Strong positive indicator
        elif primer.gel_result in ['multiple_bands', 'no_band', 'wrong_size']:
            negative += 2  # Strong negative indicator
        elif primer.gel_result == 'faint':
            negative += 1

        # qPCR results
        if primer.qpcr_result == 'amplified':
            positive += 1
        elif primer.qpcr_result == 'data_present':
            positive += 0.5  # Has data but unclear if good
        elif primer.qpcr_result in ['no_amplification', 'poor']:
            negative += 1

        # Sequencing results
        if primer.sequencing_result == 'verified':
            positive += 1
        elif primer.sequencing_result == 'data_present':
            positive += 0.5  # Has sequence data
        elif primer.sequencing_result == 'mismatch':
            negative += 1

        # BLAST results - most reliable indicator
        if primer.blast_result == 'specific':
            positive += 2  # Strong indicator of success
        elif primer.blast_result == 'partial':
            positive += 0.5
        elif primer.blast_result == 'non_specific':
            negative += 2  # Strong indicator of failure
        elif primer.blast_result == 'low_identity':
            negative += 1

        # Determine overall status
        # Note: The key insight is that if BLAST is specific and there's data,
        # the primer likely works. If BLAST shows non-specific hits, it failed.
        if negative >= 2:
            primer.validation_status = 'failed'
            primer.is_successful = False
        elif positive >= 2 and negative == 0:
            primer.validation_status = 'success'
            primer.is_successful = True
        elif positive > negative:
            primer.validation_status = 'likely_success'
            primer.is_successful = True
        elif negative > positive:
            primer.validation_status = 'likely_failed'
            primer.is_successful = False
        else:
            primer.validation_status = 'unknown'
            primer.is_successful = None

    def scrape_primers(self, primer_ids: List[str],
                       output_file: str = "validated_primers.csv") -> List[ValidatedPrimer]:
        """
        Scrape multiple primers and save results
        """
        primers = []
        output_path = self.output_dir / output_file

        # Prepare CSV
        fieldnames = [f.name for f in ValidatedPrimer.__dataclass_fields__.values()]

        try:
            self._init_driver()

            with open(output_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for primer_id in tqdm(primer_ids, desc="Scraping primers"):
                    primer = self.fetch_primer_detail(primer_id)
                    if primer is not None:
                        primers.append(primer)
                        writer.writerow(asdict(primer))
                        f.flush()

                    time.sleep(self.delay)

            logger.info(f"Scraped {len(primers)} primers to {output_path}")

        finally:
            self._close_driver()

        return primers

    def save_json(self, primers: List[ValidatedPrimer], output_file: str = "validated_primers.json"):
        """Save primers to JSON"""
        output_path = self.output_dir / output_file
        with open(output_path, 'w') as f:
            json.dump([asdict(p) for p in primers], f, indent=2)
        logger.info(f"Saved to {output_path}")


def generate_primer_ids(start_id: int, end_id: int,
                        versions: List[str] = None) -> List[str]:
    """
    Generate potential PrimerBank IDs by enumerating protein IDs

    Args:
        start_id: Starting protein ID number
        end_id: Ending protein ID number
        versions: List of version suffixes (default: ['a1', 'a2', 'a3'])

    Returns:
        List of potential primer IDs
    """
    if versions is None:
        versions = ['a1', 'a2', 'a3']

    ids = []
    for protein_id in range(start_id, end_id + 1):
        for version in versions:
            ids.append(f"{protein_id}{version}")

    return ids


def get_known_validated_primers() -> Tuple[List[str], List[str]]:
    """
    Return lists of known successful and failed primer IDs
    Based on the BMC Genomics paper examples
    """
    # From Additional file 1 - successful primers
    successful = [
        "6679201a1",   # platelet-activating factor
        "30425300a1",  # RIKEN cDNA F630035L11 gene
        "22129479a1",  # olfactory receptor MOR196-4
        "26352704a1",  # unnamed protein product
        "6755010a1",   # platelet derived growth factor
    ]

    # From Additional file 2 - failed primers (gel analysis)
    failed_gel = [
        "28972373a1",  # mKIAA0734 protein
        "23346543a1",  # granzyme N
        "12832882a1",  # unnamed protein product
        "53389a1",     # natural killer cell receptor-P1
        "12837565a1",  # unnamed protein product
    ]

    return successful, failed_gel


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Selenium-based PrimerBank scraper")
    parser.add_argument("--mode", choices=["test", "scrape", "enumerate"],
                        default="test", help="Operation mode")
    parser.add_argument("--output", default="data", help="Output directory")
    parser.add_argument("--ids-file", help="File with primer IDs to scrape")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Delay between requests (seconds)")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Run browser in headless mode")
    parser.add_argument("--start-id", type=int, help="Starting protein ID for enumeration")
    parser.add_argument("--end-id", type=int, help="Ending protein ID for enumeration")

    args = parser.parse_args()

    scraper = SeleniumPrimerBankScraper(
        output_dir=args.output,
        headless=args.headless,
        delay=args.delay
    )

    if args.mode == "test":
        # Test with known successful and failed primers
        successful_ids, failed_ids = get_known_validated_primers()
        test_ids = successful_ids + failed_ids

        logger.info(f"Testing with {len(test_ids)} known primers...")
        primers = scraper.scrape_primers(test_ids, output_file="test_primers.csv")
        scraper.save_json(primers, "test_primers.json")

        # Print summary
        success_count = sum(1 for p in primers if p.is_successful is True)
        fail_count = sum(1 for p in primers if p.is_successful is False)
        unknown_count = sum(1 for p in primers if p.is_successful is None)

        print(f"\nTest Results:")
        print(f"  Total scraped: {len(primers)}")
        print(f"  Successful: {success_count}")
        print(f"  Failed: {fail_count}")
        print(f"  Unknown: {unknown_count}")

        # Check against known labels
        print(f"\nExpected: 5 successful, 5 failed")

    elif args.mode == "scrape":
        if not args.ids_file:
            logger.error("--ids-file required for scrape mode")
            return

        with open(args.ids_file) as f:
            primer_ids = [line.strip() for line in f
                         if line.strip() and not line.startswith('#')]

        logger.info(f"Scraping {len(primer_ids)} primers...")
        primers = scraper.scrape_primers(primer_ids)
        scraper.save_json(primers)

    elif args.mode == "enumerate":
        if not args.start_id or not args.end_id:
            logger.error("--start-id and --end-id required for enumerate mode")
            return

        primer_ids = generate_primer_ids(args.start_id, args.end_id)
        logger.info(f"Generated {len(primer_ids)} primer IDs to scrape")

        # Save IDs first
        ids_file = Path(args.output) / "enumerated_ids.txt"
        with open(ids_file, 'w') as f:
            for pid in primer_ids:
                f.write(f"{pid}\n")

        # Scrape
        primers = scraper.scrape_primers(primer_ids)
        scraper.save_json(primers)


if __name__ == "__main__":
    main()
