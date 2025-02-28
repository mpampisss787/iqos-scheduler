@echo off
cd /d C:\Users\mpamp\myproject
git add .
git commit -m "Auto update: %date% %time%"
git push origin main
