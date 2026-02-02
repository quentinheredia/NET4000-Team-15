import pymysql

mydb = pymysql.connect(
    host = "localhost",
    user = "root",
    password = "terrestrial",
    autocommit = False # Implemented so that any critical adjustments later on will require "mydb.commit()" which can prevent fatal errors"
)
print(mydb)


mycursor = mydb.cursor()

print("Creating test database")

mycursor.execute("CREATE DATABASE testdatabase")

mycursor.execute("SHOW DATABASES")

for x in mycursor:
    print(x)

print("Test database created")
print("Now deleting test database")

mycursor.execute("DROP DATABASE testdatabase")
mycursor.execute("SHOW DATABASES")
for x in mycursor:
    print(x)

print("Test Database deleted")

