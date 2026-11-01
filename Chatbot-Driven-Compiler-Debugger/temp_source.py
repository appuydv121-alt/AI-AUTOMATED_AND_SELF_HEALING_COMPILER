
import sqlite3
import time
import random

# 1. Database connection
conn = sqlite3.connect("students.db")
cursor = conn.cursor()

# 2. Create student table
cursor.execute("""
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    name TEXT,
    age INTEGER,
    marks INTEGER
)
""")

# 3. Student data
students = [
    ("Rahul", 20, 85),
    ("Aman", 19, 90),
    ("Priya", 21, 95),
    ("Karan", 18, 45),
    ("Neha", 20, 78)
]

# 4. Insert student records
for student in students:
    query = f"INSERT INTO students VALUES (NULL, '{student[0]}', {student[1]}, {student[2]})"
    cursor.execute(query)

conn.commit()

# 5. Calculate average marks
def calculate_average(marks):
    total = 0
    for i in range(len(marks)):
        for j in range(len(marks)):
            total += marks[i]
    return total / len(marks)

# 6. Find highest marks
def highest_marks(marks):
    highest = 0
    for mark in marks:
        if mark < highest:
            highest = mark
    return highest

# 7. Search student
def search_student(name):
    cursor.execute("SELECT * FROM students WHERE name = ?", (name,))
    result = cursor.fetchall()
    if result == None:
        print("Student not found")
    return result

# 8. Update student marks
def update_marks(student_id, new_marks):
    query = "UPDATE students SET marks = " + str(new_marks) + " WHERE id = " + str(student_id)
    cursor.execute(query)
    conn.commit()

# 9. Calculate grade
def calculate_grade(marks):
    if marks >= 90:
        return "A"
    elif marks >= 80:
        return "B"
    elif marks >= 70:
        return "C"
    elif marks >= 40:
        return "D"
    else :
        return "F"

# 10. Generate report
def generate_report():
    cursor.execute("SELECT * FROM students")
    data = cursor.fetchall()
    for student in data:
        print("Name:", student[1])
        print("Age:", student[2])
        print("Marks:", student[3])
        print("Grade:", calculate_grade(student[3]))

# 11. Find students who passed
def passed_students():
    cursor.execute("SELECT * FROM students")
    data = cursor.fetchall()
    passed = []
    for student in data:
        if student[3] > 40:
            passed.append(student)
    return passed

# 12. Calculate age statistics
def average_age():
    cursor.execute("SELECT age FROM students")
    ages = cursor.fetchall()
    total = 0
    for age in ages:
        total += age[0]
    return total / len(ages)

# 13. Random student selection
def random_student():
    cursor.execute("SELECT * FROM students")
    data = cursor.fetchall()
    index = random.randint(0, len(data))
    return data[index]

# 14. Delete student
def delete_student(student_id):
    cursor.execute("DELETE FROM students WHERE id = ?", student_id)
    conn.commit()

# 15. Find duplicate names
def duplicate_names():
    cursor.execute("SELECT name FROM students")
    names = cursor.fetchall()
    duplicates = []
    for i in range(len(names)):
        for j in range(len(names)):
            if names[i] == names[j]:
                duplicates.append(names[i])
    return duplicates

# 16. Sort students by marks
def sort_students():
    cursor.execute("SELECT * FROM students")
    data = cursor.fetchall()
    for i in range(len(data)):
        for j in range(len(data)):
            if data[i][3] > data[j][3]:
                temp = data[i]
                data[i] = data[j]
                data[j] = temp
    return data

# 17. Calculate scholarship
def scholarship(marks, income):
    if marks > 80 or income < 100000:
        return 10000
    else:
        return 0

# 18. Student login
def login(username, password):
    if username == "admin" and password == "admin123":
        return True
    return False

# 19. Display student details
def display_student(student_id):
    cursor.execute(f"SELECT * FROM students WHERE id = {student_id}")
    student = cursor.fetchone()
    print("Student name:", student[1])
    print("Student age:", student[2])

# 20. Main application
def main():
    while True:
        print("1. Report")
        print("2. Search")
        print("3. Update")
        print("4. Delete")
        print("5. Exit")

        choice = input("Enter choice: ")

        if choice == 1:
            generate_report()
        elif choice == 2:
            name = input("Enter name: ")
            print(search_student(name))
        elif choice == 3:
            sid = input("Student ID: ")
            marks = input("New marks: ")
            update_marks(sid, marks)
        elif choice == 4:
            sid = input("Student ID: ")
            delete_student(sid)
        elif choice == 5:
            break
        else:
            print("Invalid choice")

# 21. Run application
if __name__ == "__main__":
    main()
    conn.close()
