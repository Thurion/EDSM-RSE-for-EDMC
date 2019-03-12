del /s /q *.pyo
del /s /q *.pyc

mkdir Release\EDSM-RSE
copy *.py Release\EDSM-RSE
xcopy /s psycopg2 Release\EDSM-RSE\psycopg2\
xcopy /s psycopg2-2.7.4.dist-info Release\EDSM-RSE\psycopg2-2.7.4.dist-info\
copy *.md Release\EDSM-RSE
copy LICENSE Release\EDSM-RSE\LICENSE