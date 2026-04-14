## Installation

1. Open the scraper.py file in your python IDE and create a project.

2. Create a virtual environment:
python -m venv .venv
.venv\Scripts\activate

3. Install dependancies:
pip install undetected-chromedriver selenium beautifulsoup4 requests pillow pywinauto tqdm

4. Run the script: 
python selenium_scraper.py

## Instructions

Upon launching, you will be greeted by the pipeline interface:

1. DISCOVER: Feed the script a .txt file containing a list of Gene Symbols (e.g., BRCA1). It will search the database and extract all associated PrimerBank IDs to a new text file.

2. SCRAPE: Feed the script a list of PrimerBank IDs. It will extract the metadata, biological sequences, Tm values, BLAST results, and all validation imagery.

3. TOGGLE: Turn Headless mode ON or OFF (Useful for debugging visual elements).

4. EXIT: Safely kills the Chrome driver processes and exits.

## Input File Format
When prompted for a file path, provide a standard text file with one item per line.
Example genes.txt:

Actb
Gapdh
Tp53

## File locations:

/data
  validated_primers.csv        # Master dataset (Appends automatically)
  ids_mouse.txt                # Log of discovered IDs
  done_mouse.txt               # Log of successfully searched genes

   /images
    /amp_plots              # Amplification curves
    /dissociation_curves    # Melting curves
    /gels_sample            # Extracted target bands
    /gels_ladder            # Reference ladders

## Troubleshooting and notes

"No windows for that process could be found": This error occurs if the Proton VPN client is minimized to the system tray or closed. Keep the VPN window open in the background so pywinauto can interact with it.

Missing Data in IDEs: The validated_primers.csv file can grow very large (20MB+). Code editors like PyCharm or VS Code limit text file viewing to ~2.5MB to save memory, which makes it look like data is missing. Always open the CSV in Excel or Pandas to view the complete dataset.

Dead Links: Harvard's database occasionally features broken image links. The script will attempt to load a page 3 times. If it fails 3 consecutive times, it tags the CSV row with error_retry_limit and seamlessly moves to the next primer.
