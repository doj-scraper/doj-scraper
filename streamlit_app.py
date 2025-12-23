They are even searchable!

If you're comfortable with using a CLI, here's how I did it (with a little help from ChatGPT..)

----

1. Get all PDF links from the DOJ page

The PDFs are directly embedded in the page HTML and all start with the same prefix.

wget -O court-records.html https://www.justice.gov/epstein/court-records
grep -o 'https://www.justice.gov/multimedia/Court%20Records/[^"]*\.pdf' court-records.html | sort -u > pdf_links.txt

Result: pdf_links.txt contains every PDF URL.

2. Download all PDFs without overwriting files

Many PDFs share names (e.g., 001.pdf), so we preserve the folder structure.

mkdir -p downloads
wget -c -i pdf_links.txt -x --directory-prefix=downloads --no-host-directories --cut-dirs=2

Result:
Each case gets its own folder → no filename collisions.

3. Search all PDFs for words, for example “Trump”

Install the PDF search tool once:

brew install pdfgrep

Search everything (case-insensitive, shows page numbers):

pdfgrep -R -i -n "Trump" downloads

Save results:

pdfgrep -R -i -n "Trump" downloads > trump_mentions.txt

