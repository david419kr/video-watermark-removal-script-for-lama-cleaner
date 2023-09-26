A simple script for removing video watermark, using Lama Cleaner.  
Only tested at NVIDIA windows environment.

Instructions:

Download files, as ZIP or git clone or whatever.  
Run Lama Cleaner.  
Place "video.mp4" and "mask.png" in this folder, then double click to run "run.bat".  
("mask.png" is an image which has black background and white masking)  

If not working, pls check requirements below.

-- Requirements --

> Lama Cleaner must be running at 8080 port. https://github.com/Sanster/lama-cleaner

> Python3 must be installed. (Maybe you will automatically satisfy this requirement if you're running Lama Cleaner)

> No matter what is the original extension of the video, video file name must be "video.mp4".

> "mask.png" must be the exact same resolution with video file.