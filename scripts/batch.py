import requests
import os
from multiprocessing import Process

directory = './temp/input'

mask_path = "mask.png"
mask = open(mask_path, "rb").read()

url = "http://127.0.0.1:8080/inpaint"

files = []

for filename in os.listdir(directory):
    f = os.path.join(directory, filename)
    if os.path.isfile(f):
        files.append(f)

# Print iterations progress
def printProgressBar (iteration, total, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█', printEnd = "\r"):
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end = printEnd)
    # Print New Line on Complete
    if iteration == total: 
        print()

def batch(start, end):
    printProgressBar(0, end, prefix = 'cleaning extracted frames...', suffix = 'Complete', length = 50)
    for i in range(start, end):
        img_path = files[i]
        # print(img_path)
        image = open(img_path, "rb").read()

        response = requests.post(
            url,
            files={"image": image, "mask": mask},
            data={
                "ldmSteps": 25,
                "ldmSampler": "plms",
                "hdStrategy": "Original",
                "zitsWireframe": False,
                "hdStrategyCropMargin": 128,
                "hdStrategyCropTrigerSize": 512,
                "hdStrategyResizeLimit": 1280,
                "prompt": "",
                "negativePrompt": "",
                "useCroper": False,
                "croperX": 0,
                "croperY": 0,
                "croperHeight": 512,
                "croperWidth": 512,
                "sdScale": 1.0,
                "sdMaskBlur": 0,
                "sdStrength": 0.75,
                "sdSteps": 50,
                "sdGuidanceScale": 7.5,
                "sdSampler": "uni_pc",
                "sdSeed": 42,
                "sdMatchHistograms": False,
                "cv2Flag": "INPAINT_NS",
                "cv2Radius": 4,
                "paintByExampleSteps": 50,
                "paintByExampleGuidanceScale": 7.5,
                "paintByExampleMaskBlur": 0,
                "paintByExampleSeed": 42,
                "paintByExampleMatchHistograms": False,
                "paintByExampleExampleImage": None,
                "p2pSteps": 50,
                "p2pImageGuidanceScale": 7.5,
                "p2pGuidanceScale": 7.5,
                "controlnet_conditioning_scale": 0.4,
                "controlnet_method": "control_v11p_sd15_canny",
                "paint_by_example_example_image": None,
            },
        )

        splitFilename = files[i].split('\\')[1]
        with open(f"./temp/output/{splitFilename}", "wb") as f:
            f.write(response.content)
        printProgressBar(i, end, prefix = 'cleaning extracted frames...', suffix = 'Complete', length = 50)

if __name__ == "__main__":
    print("\n")
    START, END = 0, len(files)
    th1 = Process(target=batch, args=(START, END))
    # th2 = Process(target=batch, args=(END//2, END))

    th1.start()
    # th2.start()
    th1.join()
    # th2.join()