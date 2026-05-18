import os
import shutil

data_folder = "data"
if os.path.exists(data_folder):
    shutil.rmtree(data_folder) 
os.makedirs(data_folder)

print("[INFO] Data folder cleaned.")