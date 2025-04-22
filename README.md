# SmartOCR – extremely clean AI-powered results, no matter the layout!

## What is SmartOCR?

Imagine if you could use an AI to understand and render your document for you. Well, that's what SmartOCR is. 
SmartOCR is an OCR tool powered by a visual language model. It extracts the text from a page and renders it into ASCII – no matter how complex the output is.

![Example of OCR output of a very complicated image layout](https://github.com/user-attachments/assets/f244c240-d94f-4aa5-957c-da76227650c7)

## Smart in all senses

SmartOCR isn't just smart because it is AI-powered. It was designed to do the OCR in small batches and then join the results together (this behavior can be tweaked in the settings).
This means that while it is powerful, it can also handle very long, 400+ page documents.
It also was designed with multithreading in mind, so it'll always attempt to stay as responsive as possible.

## Sounds great! How do I run it?

1. First, download [LmStudio](https://lmstudio.ai/).
2. Your next step is to download the language model. Due to how it is designed, a vision-enabled model is MANDATORY.
At the time of my writing, the most powerful language model is Gemma 3 QAT. The 12B parameter model, which is reasonable enough in most cases, will take around 6-7 GB RAM.
Download it [here](https://lmstudio.ai/model/gemma-3-12b-it-qat), clicking on the button "Use in LMStudio."

![image](https://github.com/user-attachments/assets/b0f505d1-798f-4eb1-9b55-9bc4b153a1e0)

3. When you are done, open the console and run the program with:
`python SmartOCR.py`

Install any necessary dependencies.

4. Enjoy!

## Known limitations
Please be aware that this program does not replicate the original document layout or extract any images. 
Those features are intended in the future, but are not guaranteed.


