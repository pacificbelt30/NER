from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware
import aiofiles
import os
import uvicorn
import asyncio
from ner import Textra_connection

app = FastAPI()

# CORSを回避するために追加（今回の肝）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

base = 'pdfstore/'
transfile_identifier = '_new'

def translate(filename):
    tc = Textra_connection(filename)
    tc.get_pdf_info()
    tc.post_page()
    tc.insert()
    return tc.pe.output_path

@app.post("/return_translated_file")
async def return_translated_file(file: UploadFile):
    out_file_path = os.path.join(base,file.filename)
    if os.path.exists(out_file_path):
        print(out_file_path,"removed")
        os.remove(out_file_path)
    async with aiofiles.open(out_file_path, 'wb') as out_file:
        content = await file.read()  # async read
        await out_file.write(content)  # async write
    print("fileresponse")
    # return {"name":out_file_path}
    headers = {'Access-Control-Expose-Headers': 'Content-Disposition'}

    await asyncio.sleep(5.0)
    out_file_path = translate(out_file_path)
    return FileResponse(out_file_path,filename=file.filename,media_type="application/pdf",headers=headers)

@app.post("/return_translated_file_jaen")
async def return_translated_file_jaen(file: UploadFile):
    pass

@app.post("/return_origin_file")
async def return_origin_file(file: UploadFile):
    out_file_path = os.path.join(base,file.filename)
    if os.path.exists(out_file_path):
        print(out_file_path,"removed")
        os.remove(out_file_path)
    async with aiofiles.open(out_file_path, 'wb') as out_file:
        content = await file.read()  # async read
        await out_file.write(content)  # async write
    print("fileresponse")
    # return {"name":out_file_path}
    headers = {'Access-Control-Expose-Headers': 'Content-Disposition'}
    await asyncio.sleep(5.0)
    return FileResponse(out_file_path,filename=file.filename,media_type="application/pdf",headers=headers)

if __name__ == "__main__":
    uvicorn.run("api_server:app", port=8000, reload=True, host="0.0.0.0")
