import os
import re

def replace_in_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replacements
    # Merchant -> Vendor
    new_content = re.sub(r'Merchant', 'Vendor', content)
    # merchant -> vendor
    new_content = re.sub(r'merchant', 'vendor', new_content)
    # MERCHANT -> VENDOR
    new_content = re.sub(r'MERCHANT', 'VENDOR', new_content)

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

def main():
    target_dir = r"c:\Users\owais\Downloads\MAO-GrabHack-main\vendor-onboarding-agent\frontend\src"
    for root, dirs, files in os.walk(target_dir):
        for file in files:
            if file.endswith(('.ts', '.tsx', '.html', '.css')):
                replace_in_file(os.path.join(root, file))

    # Rename directory if it exists
    merchant_dir = os.path.join(target_dir, 'views', 'merchant')
    vendor_dir = os.path.join(target_dir, 'views', 'vendor')
    if os.path.exists(merchant_dir):
        os.rename(merchant_dir, vendor_dir)
        print(f"Renamed {merchant_dir} to {vendor_dir}")

    # Rename files that might contain merchant in the name
    for root, dirs, files in os.walk(target_dir):
        for file in files:
            if 'Merchant' in file or 'merchant' in file:
                old_path = os.path.join(root, file)
                new_file = file.replace('Merchant', 'Vendor').replace('merchant', 'vendor')
                new_path = os.path.join(root, new_file)
                os.rename(old_path, new_path)
                print(f"Renamed {old_path} to {new_path}")

if __name__ == '__main__':
    main()
