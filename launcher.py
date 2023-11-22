import sys
import os
from ner import CombinePDF, TextraConnection

def help():
    helptxt = """
    this is launcher for using some NER features.
    if you want to know feature details:
        mner.py [subcommand] --help

    after translate( or combine ) subcommand toward [filename].[ext], we find [filename]_new.[ext]( or [filename]_all.[ext] ).
    subcommands:
        translate
        combine
    """
    print(helptxt)

def translate():
    tc = pdf_info()
    tc.post_page()
    tc.insert()

def pdf_info():
    filename = 'source.pdf'
    if len(sys.argv) >= 3:
        filename = sys.argv[2]
    tc = TextraConnection(filename)
    tc.get_pdf_info()
    tc.print_pdf_info()
    return tc

def combine():
    pdf = 'source.pdf'
    if len(sys.argv) >= 3:
        pdf = sys.argv[2]
    file_ext = os.path.basename(pdf).split('.')
    dir = os.path.dirname(pdf)
    trans_file = os.path.join(dir,f"{file_ext[0]}_new.{file_ext[1]}")
    target_file = os.path.join(dir,f"{file_ext[0]}_all.{file_ext[1]}")
    cpdf = CombinePDF(pdf,trans_file,target_file)
    cpdf.combine(insert_first_page=False)

def main():
    if len(sys.argv) <= 1:
        help()
        exit(0)

    match sys.argv[1]:
        case 'translate':
            translate()
        case 'combine':
            combine()
        case 'info':
            pdf_info()
        case _:
            help()
            exit(0)

if __name__ == "__main__":
    main()
