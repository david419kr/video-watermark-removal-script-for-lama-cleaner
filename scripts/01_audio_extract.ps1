mkdir .\temp
mkdir .\temp\input
mkdir .\temp\output

$codec = .\ffmpeg\bin\ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 .\video.mp4
$codec = $codec.Substring(0,3)
if ($codec -eq "vor") {$codec = "ogg"}
./ffmpeg/bin/ffmpeg -i ./video.mp4 -vn -acodec copy ./temp/audio.$codec