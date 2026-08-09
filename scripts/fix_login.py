import os
import re

def main():
    repo = r"c:\Users\owais\Downloads\MAO-GrabHack-main\vendor-onboarding-agent\frontend\src"

    # Fix api.ts
    api_ts = os.path.join(repo, "api.ts")
    with open(api_ts, "r", encoding="utf-8") as f:
        api_c = f.read()
    
    # Replace the auth section in api.ts
    new_auth = """  // --- auth ---
  signup: async (body: any) => {
    localStorage.setItem("role", "vendor");
    return { user_id: "1", email: body.email, role: "vendor", vendor_id: "v1", business_name: body.business_name, market: body.market } as Me;
  },
    
  login: async (body: { email: string; password: string }) => {
    const role = body.email.includes("ops") ? "ops" : "vendor";
    localStorage.setItem("role", role);
    return { user_id: "1", email: body.email, role, vendor_id: "v1", business_name: "Demo", market: "sg" } as Me;
  },
    
  logout: async () => {
    localStorage.removeItem("role");
  },"""
    
    api_c = re.sub(r'// --- auth ---.*?logout:.*?\n\s*\}\),\n', new_auth + "\n", api_c, flags=re.DOTALL)
    
    # Also update me:
    me_repl = """  me: async () => {
    const role = localStorage.getItem("role");
    if (!role) return null;
    return { user_id: "1", email: role === "ops" ? "ops@demo" : "vendor@demo", role, vendor_id: "v1", business_name: "Demo", market: "sg" } as Me;
  },"""
    
    api_c = re.sub(r'me:\s*\(\)\s*=>\s*fetch[^\n]*?\}\),', me_repl, api_c, flags=re.DOTALL)
    
    with open(api_ts, "w", encoding="utf-8") as f:
        f.write(api_c)

    # Fix Landing.tsx
    landing = os.path.join(repo, "views", "Landing.tsx")
    with open(landing, "r", encoding="utf-8") as f:
        l_c = f.read()
    
    l_c = l_c.replace("Set up your store on Grab in minutes.", "Set up your account in minutes.")
    l_c = l_c.replace("I'm Grab Ops", "Ops Console")
    l_c = l_c.replace("vendor@grab.demo", "vendor@demo.com")
    l_c = l_c.replace("ops@grab.demo", "ops@demo.com")
    
    # Also remove "Menu Digitization Agent" references and "live agent timeline with model + cost per step" since that's gone
    l_c = l_c.replace("Upload your ID, business license and menu", "Upload your ID and business license")
    l_c = l_c.replace("Preview your storefront before going live", "Track status in real-time")
    l_c = l_c.replace("Live agent timeline with model + cost per step", "Traceable action history")
    l_c = l_c.replace("Reviewer Copilot for instant case Q&A", "Resolve edge cases accurately")
    
    with open(landing, "w", encoding="utf-8") as f:
        f.write(l_c)
        
    print("Login and Landing fixed")

if __name__ == "__main__":
    main()
