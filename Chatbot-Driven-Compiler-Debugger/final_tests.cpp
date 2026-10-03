Test Case 1: The Malicious Injection (Security Guardrail)
#include <iostream>
#include <cstdlib>
using namespace std;

int main() {
    cout << "Welcome to the student portal." << endl;
    
    // Attempting to open the Mac calculator or run a bash command
    system("open /Applications/Calculator.app"); 
    
    return 0;
}



Test Case 2: The Cryptic Syntax Error (Path C)
#include <iostream>
using namespace std;

int main() {
    int total_marks = 95
    
    // Using a variable that was never declared
    cout << "Score: " << totalMarks << endl; 
    
    return 0;
}



Test Case 3: The Silent Crash (Path B - Agentic LLDB)
#include <iostream>
#include <vector>
using namespace std;

int main() {
    cout << "Calculating average grade..." << endl;
    
    int totalScore = 500;
    int numberOfStudents = 0; 
    
    // CRITICAL: Compiles perfectly, but divides by zero at runtime.
    // Triggers a SIGFPE (Arithmetic Exception).
    int average = totalScore / numberOfStudents;
    
    cout << "The average is: " << average << endl;
    
    return 0;
}



Test Case 4: The Logic Bug (Path A - Success + Logic Check)
#include <iostream>
using namespace std;

int main() {
    // Logic flaw: loop runs one extra time, printing 1 to 6 instead of 1 to 5.
    cout << "Printing top 5 ranks:" << endl;
    for(int i = 1; i <= 6; i++) {
        cout << "Rank " << i << endl;
    }
    
    return 0;
}