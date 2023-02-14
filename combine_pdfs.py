import fitz
import os

class PDFInfo:
    def __init__(self,path):
        self.path = path
        self.document = fitz.open(path)
        self.page = len(self.document)

class CombinePDF:
    def __init__(self,doc1,doc2,output_path):
        self.doc1 = PDFInfo(doc1)
        self.doc2 = PDFInfo(doc2)
        self.output_path = output_path

    def combine(self,insert_first_page=False):
        if self.doc1.page != self.doc2.page:
            raise ValueError('we must match num of pages between doc1 and doc2')

        new_pdf = fitz.open()
        if insert_first_page:
            # new_pdf.insert_page(0)
            new_pdf.insert_pdf(self.doc1.document,from_page=0,to_page=0)
        for p in range(self.doc1.page):
            new_pdf.insert_pdf(self.doc1.document,from_page=p,to_page=p)
            new_pdf.insert_pdf(self.doc2.document,from_page=p,to_page=p)
        new_pdf.save(self.output_path)

if __name__ == "__main__":
    pdf = "./pdf/cpu-api.pdf"
    file_ext = os.path.basename(pdf).split('.')
    dir = os.path.dirname(pdf)
    # print(os.path.join(dir,f"{file_ext[0]}_new.{file_ext[1]}"))
    trans_file = os.path.join(dir,f"{file_ext[0]}_new.{file_ext[1]}")
    target_file = os.path.join(dir,f"{file_ext[0]}_all.{file_ext[1]}")
    cpdf = CombinePDF(pdf,trans_file,target_file)
    cpdf.combine(insert_first_page=False)

