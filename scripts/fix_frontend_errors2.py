import os
import re

def main():
    repo = r"c:\Users\owais\Downloads\MAO-GrabHack-main\vendor-onboarding-agent\frontend\src"

    # Fix Status.tsx
    status_tsx = os.path.join(repo, "views", "vendor", "Status.tsx")
    with open(status_tsx, "r", encoding="utf-8") as f:
        st_content = f.read()
    st_content = st_content.replace('sum.data?.vendor_message', 'sum.data?.reviewer_summary')
    with open(status_tsx, "w", encoding="utf-8") as f:
        f.write(st_content)

    # Fix VendorShell.tsx
    shell = os.path.join(repo, "views", "vendor", "VendorShell.tsx")
    with open(shell, "r", encoding="utf-8") as f:
        sh_content = f.read()
    # Remove the confidence score block completely
    sh_content = re.sub(r'\{latestCase && null !== null && \(.*?\)\}', '', sh_content, flags=re.DOTALL)
    sh_content = sh_content.replace('&& null !== null ', '')
    with open(shell, "w", encoding="utf-8") as f:
        f.write(sh_content)

    # Fix Wizard.tsx
    wizard = os.path.join(repo, "views", "vendor", "Wizard.tsx")
    with open(wizard, "r", encoding="utf-8") as f:
        w_content = f.read()
    w_content = w_content.replace('country: me.data?.market,', 'country: me.data?.market || "",')
    w_content = w_content.replace('legal_name: me.data?.business_name,', 'legal_name: me.data?.business_name || "",')
    
    # Also fix preflightDocument call
    w_content = w_content.replace('api.preflightDocument(f, "unknown", me.data?.market, me.data?.business_name)', 'api.preflightDocument(f, "unknown", me.data?.market || undefined, me.data?.business_name || undefined)')

    with open(wizard, "w", encoding="utf-8") as f:
        f.write(w_content)
        
    print("Fixed remaining TS errors")

if __name__ == "__main__":
    main()
