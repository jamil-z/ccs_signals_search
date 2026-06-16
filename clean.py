import shutil
import os
from pathlib import Path

def main():
    output_dir = Path("./outputs")
    if output_dir.exists() and output_dir.is_dir():
        # Iterate over all files and folders in output directory
        for item in output_dir.iterdir():
            if item.is_file():
                item.unlink()
                print(f"🗑️ Deleted: {item}")
            elif item.is_dir():
                shutil.rmtree(item)
                print(f"🗑️ Deleted directory: {item}")
        print("✅ Outputs directory cleaned.")
    else:
        print("ℹ️ The outputs directory does not exist or is already clean.")

if __name__ == "__main__":
    main()
