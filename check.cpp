#include <iostream>
using namespace std;

int main() {
    int n;
    cout << "Enter a number: ";
    cin >> n;

    if (n > 0) {
        cout << "Positive number" << endl;
    }
    else if (n < 0) {
        cout << "Negative number" << endl;
    }
    else {
        cout << "Zero" << endl;
    }

    for (int i = 1; i <= 5; i++) {
        cout << i << " ";
    }

    cout << "\nProgram completed!" << endl; // Fixed missing semicolon

    return 0;
}
