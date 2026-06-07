import sys
import firebase_admin
from firebase_admin import credentials, storage
from google.api_core import exceptions

def fix_cors(bucket_name):
    cred = credentials.Certificate("firebase-sa.json")
    try:
        app = firebase_admin.get_app()
    except ValueError:
        app = firebase_admin.initialize_app(cred)
    
    bucket = storage.bucket(bucket_name)
    
    cors_config = [
        {
            "origin": ["*"],
            "method": ["GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH"],
            "maxAgeSeconds": 3600,
            "responseHeader": ["*"]
        }
    ]
    
    bucket.cors = cors_config
    try:
        bucket.patch()
        print(f"SUCCESS! CORS updated for bucket: {bucket_name}")
    except exceptions.NotFound:
        print(f"ERROR: The bucket '{bucket_name}' does not exist!")
        print("Please go to the Firebase Console -> Storage -> Get Started to create it.")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        bucket_name = sys.argv[1]
    else:
        bucket_name = input("Enter your exact Storage Bucket name (e.g. trackyouridea-45c92.appspot.com): ").strip()
    
    # Strip gs:// if they pasted it
    if bucket_name.startswith("gs://"):
        bucket_name = bucket_name[5:]
        
    fix_cors(bucket_name)
