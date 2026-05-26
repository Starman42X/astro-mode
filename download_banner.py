import urllib.request
import os

url = "https://www.esa.int/var/esa/storage/images/science_exploration/space_science/gaia/19716907-3-eng-GB/Gaia_pillars.jpg"
dest = "gaia_pillars.jpg"

try:
    print(f"Downloading {url} to {dest}...")
    # Add a user-agent to avoid HTTP 403 Forbidden errors
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    with urllib.request.urlopen(req) as response:
        with open(dest, 'wb') as f:
            f.write(response.read())
    print("Download successful!")
    print(f"File size: {os.path.getsize(dest)} bytes")
except Exception as e:
    print(f"Failed to download: {e}")
